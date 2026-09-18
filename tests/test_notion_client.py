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


def test_iter_search_follows_all_pages():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("start_cursor"))
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "results": [{"object": "page", "id": "p1"}],
                    "has_more": True,
                    "next_cursor": "cursor-1",
                },
            )
        return httpx.Response(
            200,
            json={"results": [{"object": "data_source", "id": "ds1"}], "has_more": False},
        )

    settings = Settings(notion_token="secret-token")
    client = NotionClient(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert list(client.iter_search()) == [
        {"object": "page", "id": "p1"},
        {"object": "data_source", "id": "ds1"},
    ]
    assert calls == [None, "cursor-1"]
