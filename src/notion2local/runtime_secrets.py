from __future__ import annotations

import os
import tempfile
from pathlib import Path


def read_secret_file(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def write_secret_file(path: Path, value: str) -> None:
    secret = value.strip()
    if not secret:
        raise ValueError("secret value cannot be empty")

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{secret}\n")
        os.replace(temporary_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def delete_secret_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
