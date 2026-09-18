from __future__ import annotations

from pathlib import Path

from notion2local.config import Settings
from notion2local.db import Database
from notion2local.models import GraphEdge, NotionObject, ObjectSnapshot, SyncRoot
from notion2local.sync.service import SyncService

ROOT_ID = "11111111-1111-1111-1111-111111111111"
BLOCK_ID = "22222222-2222-2222-2222-222222222222"


class FakeNotionClient:
    def __init__(self):
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

    def close(self):
        pass

    def retrieve_page(self, object_id):
        return self.pages[object_id]

    def retrieve_block(self, object_id):
        return self.blocks[object_id]

    def iter_block_children(self, object_id):
        if object_id == ROOT_ID:
            yield self.blocks[BLOCK_ID]

    def iter_comments(self, object_id):
        yield from ()


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
