from __future__ import annotations

import json

from notion2local.config import Settings
from notion2local.storage.raw import RawSnapshotStore


def test_raw_snapshot_is_content_addressed_and_self_describing(tmp_path):
    settings = Settings(
        raw_storage_path=tmp_path / "raw",
        blob_storage_path=tmp_path / "blobs",
        notion_api_version="2026-03-11",
    )
    store = RawSnapshotStore(settings)
    snapshot = store.save("page", "page-1", {"object": "page", "id": "page-1"})

    payload = json.loads((tmp_path / "raw" / "objects" / "page" / "page-1").glob("*.json").__next__().read_text())
    assert snapshot.content_hash == payload["content_hash"]
    assert payload["api_version"] == "2026-03-11"
    assert payload["payload"]["id"] == "page-1"
