from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import get_settings
from .db import Database
from .models import SyncRoot
from .sync.tasks import enqueue_task


logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    database = Database(settings)
    if settings.db_auto_create:
        database.create_all()
    zone = ZoneInfo(settings.sync_timezone)
    last_slot: str | None = None
    try:
        while True:
            now = datetime.now(zone)
            slot = now.strftime("%Y-%m-%d %H:%M")
            if now.strftime("%H:%M") == settings.reconcile_time and slot != last_slot:
                with database.session() as session:
                    roots = session.query(SyncRoot).filter(SyncRoot.status != "disabled").all()
                    for root in roots:
                        enqueue_task(
                            session,
                            root_id=root.id,
                            task_type="root_sync",
                            idempotency_key=f"reconcile:{root.id}:{now.date().isoformat()}",
                            payload={"kind": "reconcile"},
                        )
                last_slot = slot
                logger.info("scheduled reconciliation roots=%s local_time=%s", len(roots), now.isoformat())
            time.sleep(max(5, settings.sync_poll_seconds))
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
