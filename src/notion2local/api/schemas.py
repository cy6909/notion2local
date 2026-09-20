from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from ..utils import normalize_notion_id


class RootCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    root_object_id: str
    strategy: Literal["fast", "balanced", "deep"] = "balanced"
    timezone: str = "Asia/Shanghai"
    include_comments: bool = True
    include_assets: bool = True

    @field_validator("root_object_id")
    @classmethod
    def validate_root_object_id(cls, value: str) -> str:
        return normalize_notion_id(value)


class AdminLogin(BaseModel):
    setup_token: SecretStr = Field(min_length=1, max_length=500)


class NotionTokenUpdate(BaseModel):
    token: SecretStr = Field(min_length=1, max_length=500)


class RootResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    root_object_id: str
    status: str
    strategy: str
    timezone: str
    include_comments: bool
    include_assets: bool
    last_sync_at: datetime | None
    last_reconcile_at: datetime | None


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    root_id: str
    kind: str
    status: str
    stats_json: dict[str, Any]
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ObjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_key: str
    object_id: str
    object_type: str
    root_id: str | None
    parent_object_id: str | None
    title: str | None
    source_url: str | None
    sync_state: str
    current_hash: str | None
    last_seen_at: datetime | None
    updated_at: datetime


class ObjectDetail(ObjectSummary):
    current_payload: dict[str, Any] | None
    current_raw_path: str | None
    in_trash: bool
    archived: bool
    has_children: bool
    edges: list[dict[str, Any]]


class LibraryBlock(BaseModel):
    record_key: str
    object_id: str
    object_type: str
    parent_object_id: str | None
    title: str | None
    sync_state: str
    position: int | None
    depth: int
    current_payload: dict[str, Any] | None
    in_trash: bool
    archived: bool
    has_children: bool


class LibraryPageContent(BaseModel):
    page: ObjectDetail
    blocks: list[LibraryBlock]
    truncated: bool
