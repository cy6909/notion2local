from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from notion2local.api.app import create_app
from notion2local.config import Settings
from notion2local.db import Database


def make_client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        raw_storage_path=tmp_path / "raw",
        blob_storage_path=tmp_path / "blobs",
        notion_token="test-token",
        notion_webhook_secret="webhook-secret",
        setup_token="setup-secret",
        db_auto_create=True,
    )
    database = Database(settings)
    return TestClient(create_app(settings, database))


def test_setup_status_does_not_return_secret(tmp_path):
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/setup/status")
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "needs_root_scope"
    assert "test-token" not in response.text
    assert "setup-secret" not in response.text


def test_webhook_signature_is_verified_and_idempotent(tmp_path):
    with make_client(tmp_path) as client:
        body = json.dumps(
            {
                "id": "evt-1",
                "type": "page.content_updated",
                "data": {"object": {"id": "11111111-1111-1111-1111-111111111111"}},
            }
        ).encode()
        signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
        headers = {"X-Notion-Signature": f"sha256={signature}"}
        first = client.post("/webhooks/notion", content=body, headers=headers)
        second = client.post("/webhooks/notion", content=body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
