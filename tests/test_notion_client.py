from __future__ import annotations

import httpx

from notion2local.config import Settings
from notion2local.notion.client import NotionClient


def test_client_sends_version_and_auth_headers():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200, json={"object": "page", "id": "p1"})

    settings = Settings(notion_token="secret-token", notion_api_version="2026-03-11")
    client = NotionClient(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    response = client.retrieve_page("p1")

    assert response["id"] == "p1"
    assert captured["authorization"] == "Bearer secret-token"
    assert captured["notion-version"] == "2026-03-11"
