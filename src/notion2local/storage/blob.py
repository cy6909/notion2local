from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings


@dataclass(frozen=True)
class BlobRef:
    sha256: str
    path: str
    byte_size: int


class BlobStore:
    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.blob_storage_path)

    def put(self, content: bytes) -> BlobRef:
        digest = hashlib.sha256(content).hexdigest()
        target = self.root / "sha256" / digest[:2] / digest[2:4] / digest
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, target)
        return BlobRef(sha256=digest, path=str(target), byte_size=len(content))
