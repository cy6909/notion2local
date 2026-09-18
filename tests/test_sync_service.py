from __future__ import annotations

from pathlib import Path

from notion2local.config import Settings
from notion2local.db import Database
from notion2local.models import GraphEdge, NotionObject, ObjectSnapshot, SyncRoot
from notion2local.sync.service import WORKSPACE_ROOT_ID, SyncService

ROOT_ID = "11111111-1111-1111-1111-111111111111"
BLOCK_ID = "22222222-2222-2222-2222-222222222222"
DATABASE_ID = "33333333-3333-3333-3333-333333333333"
DATA_SOURCE_ID = "44444444-4444-4444-4444-444444444444"
ROW_ID = "55555555-5555-5555-5555-555555555555"
EXTRA_PAGE_ID = "66666666-6666-6666-6666-666666666666"


class FakeNotionClient:
    def __init__(self, *, include_database: bool = False, include_workspace_page: bool = False):
        self.include_database = include_database
        self.pages = {
            ROOT_ID: {
                "object": "page",
                "id": ROOT_ID,
                "url": "https://notion.so/root",
                "last_edited_time": "2026-09-18T00:00:00.000Z",
                "has_children": True,
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Root"}]}},
                "parent": {"type": "workspace", "workspace": True},
            }
        }
        if include_workspace_page:
            self.pages[EXTRA_PAGE_ID] = {
                "object": "page",
                "id": EXTRA_PAGE_ID,
                "url": "https://notion.so/extra",
                "last_edited_time": "2026-09-18T00:01:00.000Z",
                "has_children": False,
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Extra"}]}},
                "parent": {"type": "workspace", "workspace": True},
            }
        self.blocks = {
            BLOCK_ID: {
                "object": "block",
                "id": BLOCK_ID,
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "Hello"}]},
                "parent": {"type": "page_id", "page_id": ROOT_ID},
            }
        }
        self.databases = {
            DATABASE_ID: {
                "object": "database",
                "id": DATABASE_ID,
                "title": [{"plain_text": "Notes"}],
                "data_sources": [{"id": DATA_SOURCE_ID}],
                "parent": {"type": "page_id", "page_id": ROOT_ID},
            }
        }
        self.data_sources = {
            DATA_SOURCE_ID: {
                "object": "data_source",
                "id": DATA_SOURCE_ID,
                "parent": {"type": "database_id", "database_id": DATABASE_ID},
            }
        }
        self.rows = {
            ROW_ID: {
                "object": "page",
                "id": ROW_ID,
                "url": "https://notion.so/row",
                "last_edited_time": "2026-09-18T00:00:00.000Z",
                "has_children": False,
                "properties": {
                    "title": {"type": "title", "title": [{"plain_text": "Row"}]}
                },
                "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE_ID},
            }
        }

    def close(self):
        pass

    def retrieve_page(self, object_id):
        return {**self.pages, **self.rows}[object_id]

    def retrieve_block(self, object_id):
        if object_id == DATABASE_ID and self.include_database:
            return {
                "object": "block",
                "id": DATABASE_ID,
                "type": "child_database",
                "has_children": False,
                "child_database": {"title": "Notes"},
                "parent": {"type": "page_id", "page_id": ROOT_ID},
            }
        return self.blocks[object_id]

    def retrieve_database(self, object_id):
        return self.databases[object_id]

    def retrieve_data_source(self, object_id):
        return self.data_sources[object_id]

    def iter_block_children(self, object_id):
        if object_id == ROOT_ID:
            yield self.blocks[BLOCK_ID]
            if self.include_database:
                yield {
                    "object": "block",
                    "id": DATABASE_ID,
                    "type": "child_database",
                    "has_children": False,
                    "child_database": {"title": "Notes"},
                    "parent": {"type": "page_id", "page_id": ROOT_ID},
                }

    def iter_data_source_rows(self, object_id):
        if object_id == DATA_SOURCE_ID:
            yield self.rows[ROW_ID]

    def iter_comments(self, object_id):
        yield from ()

    def iter_search(self):
        yield from self.pages.values()


def test_sync_is_idempotent_and_preserves_raw_history(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        raw_storage_path=tmp_path / "raw",
        blob_storage_path=tmp_path / "blobs",
        notion_token="test-token",
        db_auto_create=True,
    )
    database = Database(settings)
    database.create_all()
    client = FakeNotionClient()
    service = SyncService(settings, client=client)

    with database.session() as session:
        root = SyncRoot(name="Test", root_object_id=ROOT_ID)
        session.add(root)
        session.flush()
        root_pk = root.id

    with database.session() as session:
        first = service.sync_root(session, root_pk)
        assert first.status == "succeeded"
        assert first.stats_json["changed"] == 2

    with database.session() as session:
        second = service.sync_root(session, root_pk)
        assert second.status == "succeeded"
        assert second.stats_json["unchanged"] == 2
        assert session.query(ObjectSnapshot).count() == 2
        assert session.query(NotionObject).count() == 2
        assert session.query(GraphEdge).filter(GraphEdge.edge_type == "parent").count() == 1

    assert list((tmp_path / "raw" / "objects").rglob("*.json"))
    service.close()
    database.dispose()


def test_sync_crawls_data_source_rows(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'rows.db'}",
        raw_storage_path=tmp_path / "raw",
        blob_storage_path=tmp_path / "blobs",
        notion_token="test-token",
        db_auto_create=True,
    )
    database = Database(settings)
    database.create_all()
    service = SyncService(settings, client=FakeNotionClient(include_database=True))

    with database.session() as session:
        root = SyncRoot(name="Rows", root_object_id=ROOT_ID)
        session.add(root)
        session.flush()
        root_pk = root.id

    with database.session() as session:
        run = service.sync_root(session, root_pk)
        assert run.status == "succeeded"
        assert run.stats_json["rows"] == 1
        assert session.get(NotionObject, f"page:{ROW_ID}") is not None
        assert session.get(NotionObject, f"database:{DATABASE_ID}").title == "Notes"

    service.close()
    database.dispose()


def test_workspace_sync_discovers_all_search_results(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'workspace.db'}",
        raw_storage_path=tmp_path / "raw",
        blob_storage_path=tmp_path / "blobs",
        notion_token="test-token",
        db_auto_create=True,
    )
    database = Database(settings)
    database.create_all()
    service = SyncService(settings, client=FakeNotionClient(include_workspace_page=True))

    with database.session() as session:
        root = SyncRoot(name="Workspace", root_object_id=WORKSPACE_ROOT_ID)
        session.add(root)
        session.flush()
        root_pk = root.id

    with database.session() as session:
        first = service.sync_root(session, root_pk)
        assert first.status == "succeeded"
        assert first.stats_json["discovered"] == 2
        assert session.get(NotionObject, f"page:{EXTRA_PAGE_ID}") is not None

    with database.session() as session:
        second = service.sync_root(session, root_pk, kind="reconcile")
        assert second.status == "succeeded"
        assert second.stats_json["unchanged"] == 3
        assert session.query(ObjectSnapshot).count() == 3

    service.close()
    database.dispose()
