from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from ..config import Settings


class NotionNotConfigured(RuntimeError):
    """Raised when a sync operation is requested without a Notion credential."""


class NotionAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class NotionClient:
    """Small synchronous adapter around the versioned Notion REST API.

    The client intentionally returns raw dictionaries. Normalization is performed by the
    sync layer, so unknown fields survive API upgrades and can be re-rendered later.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.Client(timeout=settings.http_timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> NotionClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        token = self.settings.notion_token_value
        if not token:
            raise NotionNotConfigured("NOTION_TOKEN is not configured")
        return {
            "Authorization": f"Bearer {token}",
            "Notion-Version": self.settings.notion_api_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._client.request(
            method,
            f"{self.settings.notion_api_base_url.rstrip('/')}/{path.lstrip('/')}",
            headers=self._headers(),
            params=params,
            json=json_body,
        )
        if response.status_code >= 400:
            retry_after = self._retry_after(response)
            detail = response.text[:2000]
            raise NotionAPIError(response.status_code, f"Notion API {response.status_code}: {detail}", retry_after)
        return response.json()

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self.request("GET", f"/pages/{page_id}")

    def retrieve_block(self, block_id: str) -> dict[str, Any]:
        return self.request("GET", f"/blocks/{block_id}")

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self.request("GET", f"/databases/{database_id}")

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self.request("GET", f"/data_sources/{data_source_id}")

    def retrieve_user(self, user_id: str) -> dict[str, Any]:
        return self.request("GET", f"/users/{user_id}")

    def retrieve_file_upload(self, file_upload_id: str) -> dict[str, Any]:
        return self.request("GET", f"/file_uploads/{file_upload_id}")

    def list_comments(self, block_id: str, *, start_cursor: str | None = None) -> dict[str, Any]:
        params = {"block_id": block_id}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return self.request("GET", "/comments", params=params)

    def list_block_children(self, block_id: str, *, start_cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {"page_size": "100"}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return self.request("GET", f"/blocks/{block_id}/children", params=params)

    def query_data_source(
        self, data_source_id: str, *, start_cursor: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self.request("POST", f"/data_sources/{data_source_id}/query", json_body=body)

    def search(self, *, query: str | None = None, start_cursor: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"page_size": 100}
        if query:
            body["query"] = query
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self.request("POST", "/search", json_body=body)

    def iter_paginated(
        self,
        fetch_page: Any,
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            response = fetch_page(cursor)
            for result in response.get("results", []):
                yield result
            if not response.get("has_more"):
                return
            next_cursor = response.get("next_cursor")
            if not next_cursor or next_cursor == cursor:
                return
            cursor = next_cursor

    def iter_block_children(self, block_id: str) -> Iterator[dict[str, Any]]:
        yield from self.iter_paginated(lambda cursor: self.list_block_children(block_id, start_cursor=cursor))

    def iter_data_source_rows(self, data_source_id: str) -> Iterator[dict[str, Any]]:
        yield from self.iter_paginated(lambda cursor: self.query_data_source(data_source_id, start_cursor=cursor))

    def iter_comments(self, block_id: str) -> Iterator[dict[str, Any]]:
        yield from self.iter_paginated(lambda cursor: self.list_comments(block_id, start_cursor=cursor))

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    @staticmethod
    def retry_delay(error: NotionAPIError, attempt: int) -> float:
        if error.retry_after is not None:
            return min(error.retry_after, 300.0)
        return min(2.0**attempt, 300.0) + (0.1 * attempt)


def backoff_sleep(error: NotionAPIError, attempt: int) -> None:
    time.sleep(NotionClient.retry_delay(error, attempt))
