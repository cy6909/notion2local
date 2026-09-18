from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..utils import content_hash, safe_object_id


@dataclass(frozen=True)
class RawSnapshot:
    path: str
    content_hash: str
    captured_at: datetime


class RawSnapshotStore:
    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.raw_storage_path)
        self.api_version = settings.notion_api_version

    def save(self, object_type: str, object_id: str, payload: dict[str, Any]) -> RawSnapshot:
        captured_at = datetime.now(timezone.utc)
        digest = content_hash(payload)
        timestamp = captured_at.strftime("%Y%m%dT%H%M%S%fZ")
        target = (
            self.root
            / "objects"
            / safe_object_id(object_type)
            / safe_object_id(object_id)
            / f"{timestamp}-{digest[:16]}.json"
        )
        envelope = {
            "snapshot_version": 1,
            "object_type": object_type,
            "object_id": object_id,
            "api_version": self.api_version,
            "captured_at": captured_at.isoformat(),
            "content_hash": digest,
            "payload": payload,
        }
        self._atomic_write_json(target, envelope)
        return RawSnapshot(path=str(target), content_hash=digest, captured_at=captured_at)

    @staticmethod
    def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
