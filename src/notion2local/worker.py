from __future__ import annotations

import logging
import time

from .config import get_settings
from .db import Database
from .sync.service import SyncService
from .sync.tasks import claim_next_task, mark_task_failed, mark_task_succeeded


logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def process_one(database: Database, service: SyncService) -> bool:
    with database.session() as session:
        task = claim_next_task(session)
        if task is None:
            return False
        task_id = task.id
        task_type = task.task_type
        root_id = task.root_id
        object_id = task.object_id
        object_kind = str(task.payload.get("object_kind", "page"))

    try:
        with database.session() as work_session:
            if task_type == "root_sync":
                service.sync_root(
                    work_session,
                    root_id,
                    kind=str(task.payload.get("kind", "queued")),
                    run_id=task.payload.get("run_id"),
                )
            elif task_type == "object_sync" and object_id:
                service.sync_object(work_session, root_id, object_id, object_kind)
            else:
                raise ValueError(f"unsupported task type or missing object_id: {task_type}")
        with database.session() as session:
            mark_task_succeeded(session, task_id)
        logger.info("sync task succeeded task_id=%s type=%s", task_id, task_type)
    except Exception as exc:
        error = str(exc).replace("NOTION_TOKEN", "[redacted]")
        with database.session() as session:
            mark_task_failed(session, task_id, error)
        logger.warning("sync task failed task_id=%s type=%s error=%s", task_id, task_type, error)
    return True


def main() -> None:
    settings = get_settings()
    database = Database(settings)
    if settings.db_auto_create:
        database.create_all()
    service = SyncService(settings)
    try:
        while True:
            if not process_one(database, service):
                time.sleep(max(1, settings.sync_poll_seconds))
    finally:
        service.close()
        database.dispose()


if __name__ == "__main__":
    main()
