from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SyncTask
from ..utils import utc_now


def enqueue_task(
    session: Session,
    *,
    root_id: str,
    task_type: str,
    idempotency_key: str,
    object_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> SyncTask:
    existing = session.scalar(select(SyncTask).where(SyncTask.idempotency_key == idempotency_key))
    if existing:
        if existing.status in {"failed", "dead_letter"}:
            existing.status = "pending"
            existing.not_before = utc_now()
            existing.last_error = None
        return existing
    task = SyncTask(
        root_id=root_id,
        task_type=task_type,
        object_id=object_id,
        idempotency_key=idempotency_key,
        payload=payload or {},
        status="pending",
        not_before=utc_now(),
    )
    session.add(task)
    session.flush()
    return task


def claim_next_task(session: Session) -> SyncTask | None:
    task = session.scalar(
        select(SyncTask)
        .where(
            SyncTask.status == "pending",
            SyncTask.not_before <= utc_now(),
        )
        .order_by(SyncTask.created_at)
        .with_for_update(skip_locked=True)
    )
    if task is None:
        return None
    task.status = "running"
    task.attempts += 1
    session.flush()
    return task


def mark_task_succeeded(session: Session, task_id: str) -> None:
    task = session.get(SyncTask, task_id)
    if task:
        task.status = "succeeded"
        task.last_error = None
        task.updated_at = utc_now()


def mark_task_failed(session: Session, task_id: str, error: str, *, max_attempts: int = 6) -> None:
    task = session.get(SyncTask, task_id)
    if not task:
        return
    task.last_error = error[:2000]
    if task.attempts >= max_attempts:
        task.status = "dead_letter"
    else:
        task.status = "pending"
        task.not_before = utc_now() + timedelta(seconds=min(300, 2**task.attempts))
    task.updated_at = utc_now()
