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
        notion_token_file=tmp_path / "runtime" / "notion_token",
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
    assert data["state"] == "needs_workspace_initialization"
    assert "test-token" not in response.text
    assert "setup-secret" not in response.text


def test_admin_console_manages_runtime_token_without_echoing_it(tmp_path):
    with make_client(tmp_path) as client:
        page = client.get("/admin")
        assert page.status_code == 200
        assert "Notion Token" in page.text

        invalid = client.post("/api/v1/admin/session", json={"setup_token": "wrong"})
        assert invalid.status_code == 401

        login = client.post("/api/v1/admin/session", json={"setup_token": "setup-secret"})
        assert login.status_code == 200
        saved = client.put("/api/v1/admin/notion-token", json={"token": "runtime-secret"})
        assert saved.status_code == 200
        assert saved.json()["notion_token_source"] == "runtime_file"
        assert "runtime-secret" not in saved.text

        status = client.get("/api/v1/setup/status")
        assert status.json()["notion_token_source"] == "runtime_file"
        assert (tmp_path / "runtime" / "notion_token").read_text(encoding="utf-8").strip() == "runtime-secret"

        removed = client.delete("/api/v1/admin/notion-token")
        assert removed.status_code == 200
        assert not (tmp_path / "runtime" / "notion_token").exists()


def test_workspace_initialization_is_idempotent(tmp_path):
    with make_client(tmp_path) as client:
        login = client.post("/api/v1/admin/session", json={"setup_token": "setup-secret"})
        assert login.status_code == 200

        first = client.post("/api/v1/sync/workspace")
        assert first.status_code == 202
        assert first.json()["kind"] == "initial"

        second = client.post("/api/v1/sync/workspace")
        assert second.status_code == 202
        assert second.json()["id"] == first.json()["id"]

        status = client.get("/api/v1/setup/status").json()
        assert status["state"] == "needs_workspace_initialization"
        assert status["workspace_configured"] is True
        assert status["workspace_initialized"] is False
        assert status["root_count"] == 1


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
