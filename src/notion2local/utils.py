from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def safe_object_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def normalize_notion_id(value: str) -> str:
    compact = value.strip().replace("-", "")
    if len(compact) != 32 or any(char not in "0123456789abcdefABCDEF" for char in compact):
        raise ValueError("Notion object ID must be a 32-character hexadecimal UUID")
    return f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:32]}".lower()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
