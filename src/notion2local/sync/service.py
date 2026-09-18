from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import GraphEdge, NotionObject, ObjectSnapshot, SyncRoot, SyncRun
from ..notion.client import NotionAPIError, NotionClient
from ..storage.raw import RawSnapshotStore
from ..utils import content_hash, parse_iso_datetime, utc_now


@dataclass
class SyncStats:
    seen: int = 0
    changed: int = 0
    unchanged: int = 0
    edges: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seen": self.seen,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "edges": self.edges,
            "errors": self.errors,
        }


class SyncService:
    """Fetch Notion objects and atomically update the local current projection.

    The service deliberately keeps the raw response beside the normalized row. The normalized
    row is a query index, never the only copy of a Notion object.
    """

    def __init__(
        self,
        settings: Settings,
        client: NotionClient | None = None,
        raw_store: RawSnapshotStore | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or NotionClient(settings)
        self.raw_store = raw_store or RawSnapshotStore(settings)

    def close(self) -> None:
        self.client.close()

    def sync_root(
        self,
        session: Session,
        root_id: str,
        *,
        kind: str = "initial",
        run_id: str | None = None,
    ) -> SyncRun:
        root = session.get(SyncRoot, root_id)
        if root is None:
            raise ValueError(f"sync root not found: {root_id}")

        run = session.get(SyncRun, run_id) if run_id else None
        if run is None:
            run = SyncRun(root_id=root.id, kind=kind, status="running", started_at=utc_now())
            session.add(run)
        else:
            run.kind = kind
            run.status = "running"
            run.started_at = utc_now()
        root.status = "syncing"
        session.flush()

        stats = SyncStats()
        queue: deque[tuple[str, str, str | None, int | None, int]] = deque(
            [("page", root.root_object_id, None, None, 0)]
        )
        visited: set[tuple[str, str]] = set()

        try:
            while queue:
                object_kind, object_id, parent_id, position, depth = queue.popleft()
                visit_key = (object_kind, object_id)
                if visit_key in visited or depth > self.settings.max_sync_depth:
                    continue
                visited.add(visit_key)
                try:
                    payload = self._fetch(object_kind, object_id)
                    record_key = self._persist_payload(
                        session,
                        root,
                        payload,
                        parent_id=parent_id,
                        position=position,
                        stats=stats,
                    )
                    self._discover_children(
                        session,
                        root,
                        payload,
                        record_key=record_key,
                        queue=queue,
                        depth=depth,
                        include_comments=root.include_comments,
                        stats=stats,
                    )
                except Exception as exc:  # object-level failure must not hide other objects
                    stats.errors.append(
                        {
                            "object_kind": object_kind,
                            "object_id": object_id,
                            "error": self._safe_error(exc),
                        }
                    )

            root.last_sync_at = utc_now()
            root.status = "degraded" if stats.errors else "synced"
            run.status = "partial" if stats.errors else "succeeded"
            run.stats_json = stats.as_dict()
            run.finished_at = utc_now()
            session.flush()
            return run
        except Exception as exc:
            root.status = "degraded"
            run.status = "failed"
            run.error_message = self._safe_error(exc)
            run.stats_json = stats.as_dict()
            run.finished_at = utc_now()
            session.flush()
            raise

    def sync_object(
        self,
        session: Session,
        root_id: str,
        object_id: str,
        object_kind: str = "page",
    ) -> SyncStats:
        root = session.get(SyncRoot, root_id)
        if root is None:
            raise ValueError(f"sync root not found: {root_id}")
        stats = SyncStats()
        payload = self._fetch(object_kind, object_id)
        record_key = self._persist_payload(session, root, payload, stats=stats)
        queue: deque[tuple[str, str, str | None, int | None, int]] = deque()
        self._discover_children(
            session,
            root,
            payload,
            record_key=record_key,
            queue=queue,
            depth=0,
            include_comments=root.include_comments,
            stats=stats,
        )
        while queue:
            child_kind, child_id, parent_id, position, depth = queue.popleft()
            if depth > self.settings.max_sync_depth:
                continue
            child_payload = self._fetch(child_kind, child_id)
            child_key = self._persist_payload(
                session,
                root,
                child_payload,
                parent_id=parent_id,
                position=position,
                stats=stats,
            )
            self._discover_children(
                session,
                root,
                child_payload,
                record_key=child_key,
                queue=queue,
                depth=depth,
                include_comments=root.include_comments,
                stats=stats,
            )
        return stats

    def _fetch(self, object_kind: str, object_id: str) -> dict[str, Any]:
        def call() -> dict[str, Any]:
            if object_kind == "page":
                return self.client.retrieve_page(object_id)
            if object_kind == "block":
                return self.client.retrieve_block(object_id)
            if object_kind == "database":
                return self.client.retrieve_database(object_id)
            if object_kind == "data_source":
                return self.client.retrieve_data_source(object_id)
            if object_kind == "user":
                return self.client.retrieve_user(object_id)
            if object_kind == "file_upload":
                return self.client.retrieve_file_upload(object_id)
            raise ValueError(f"unsupported Notion object kind: {object_kind}")

        for attempt in range(4):
            try:
                return call()
            except NotionAPIError as exc:
                if exc.status_code not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                    raise
                time.sleep(min(NotionClient.retry_delay(exc, attempt), 10.0))
        raise AssertionError("unreachable")

    def _persist_payload(
        self,
        session: Session,
        root: SyncRoot,
        payload: dict[str, Any],
        *,
        parent_id: str | None = None,
        position: int | None = None,
        stats: SyncStats,
    ) -> str:
        object_id = str(payload.get("id") or "")
        if not object_id:
            raise ValueError("Notion response does not contain an object id")
        object_kind = str(payload.get("object") or payload.get("type") or "unknown")
        record_key = self.record_key(object_kind, object_id)
        digest = content_hash(payload)
        current = session.get(NotionObject, record_key)
        stats.seen += 1

        if current is None:
            current = NotionObject(
                record_key=record_key,
                object_id=object_id,
                object_type=object_kind,
                root_id=root.id,
            )
            session.add(current)
        else:
            current.root_id = root.id
            current.object_type = object_kind

        current.title = self._extract_title(payload)
        current.source_url = payload.get("url")
        current.remote_last_edited_at = parse_iso_datetime(payload.get("last_edited_time"))
        current.in_trash = bool(payload.get("in_trash", False))
        current.archived = bool(payload.get("archived", False))
        current.has_children = bool(payload.get("has_children", False))
        current.last_seen_at = utc_now()
        current.deleted_at = utc_now() if current.in_trash else None
        current.sync_state = "trashed" if current.in_trash else "current"

        if current.current_hash == digest and current.current_raw_path:
            stats.unchanged += 1
        else:
            snapshot = self.raw_store.save(object_kind, object_id, payload)
            session.query(ObjectSnapshot).filter(
                ObjectSnapshot.object_key == record_key,
                ObjectSnapshot.is_current.is_(True),
            ).update({"is_current": False}, synchronize_session=False)
            session.add(
                ObjectSnapshot(
                    object_key=record_key,
                    api_version=self.settings.notion_api_version,
                    content_hash=digest,
                    raw_path=snapshot.path,
                    payload=payload,
                    is_current=True,
                    captured_at=snapshot.captured_at,
                )
            )
            current.current_hash = digest
            current.current_raw_path = snapshot.path
            current.current_payload = payload
            stats.changed += 1

        session.flush()
        effective_parent_id = parent_id or self._extract_parent_id(payload)
        if effective_parent_id:
            parent_kind = self._extract_parent_kind(payload) or "unknown"
            if parent_id:
                parent_kind = "block" if parent_kind == "unknown" else parent_kind
            parent_key = self.record_key(parent_kind, effective_parent_id)
            current.parent_object_id = effective_parent_id
            self._upsert_edge(
                session,
                root_id=root.id,
                from_key=parent_key,
                to_key=record_key,
                edge_type="parent",
                position=position,
                metadata={"parent_object_id": effective_parent_id},
            )
            stats.edges += 1

        for edge_type, target_id, metadata in self._relation_edges(payload):
            self._upsert_edge(
                session,
                root_id=root.id,
                from_key=record_key,
                to_key=self.record_key("page", target_id),
                edge_type=edge_type,
                position=None,
                metadata=metadata,
            )
            stats.edges += 1
        return record_key

    def _discover_children(
        self,
        session: Session,
        root: SyncRoot,
        payload: dict[str, Any],
        *,
        record_key: str,
        queue: deque[tuple[str, str, str | None, int | None, int]],
        depth: int,
        include_comments: bool,
        stats: SyncStats,
    ) -> None:
        object_kind = str(payload.get("object") or payload.get("type") or "")
        object_id = str(payload.get("id") or "")
        if object_kind == "page" or (
            object_kind == "block" and bool(payload.get("has_children"))
        ):
            for position, child in enumerate(self.client.iter_block_children(object_id)):
                child_id = str(child.get("id") or "")
                if not child_id:
                    continue
                queue.append(("block", child_id, object_id, position, depth + 1))
                child_type = child.get("type")
                if child_type == "child_page":
                    queue.append(("page", child_id, object_id, position, depth + 1))
                elif child_type == "child_database":
                    queue.append(("database", child_id, object_id, position, depth + 1))

        if include_comments and object_id and object_kind in {"page", "block"}:
            for comment in self.client.iter_comments(object_id):
                comment_id = str(comment.get("id") or "")
                if comment_id:
                    self._persist_payload(
                        session,
                        root,
                        comment,
                        parent_id=object_id,
                        stats=stats,
                    )

    @staticmethod
    def _extract_parent_id(payload: dict[str, Any]) -> str | None:
        parent = payload.get("parent")
        if not isinstance(parent, dict):
            return None
        for key in ("page_id", "block_id", "database_id", "data_source_id"):
            if parent.get(key):
                return str(parent[key])
        return None

    @staticmethod
    def _extract_parent_kind(payload: dict[str, Any]) -> str | None:
        parent = payload.get("parent")
        if not isinstance(parent, dict):
            return None
        for key, kind in (
            ("page_id", "page"),
            ("block_id", "block"),
            ("database_id", "database"),
            ("data_source_id", "data_source"),
        ):
            if parent.get(key):
                return kind
        return None

    @staticmethod
    def _extract_title(payload: dict[str, Any]) -> str | None:
        if payload.get("object") == "page":
            properties = payload.get("properties") or {}
            for prop in properties.values():
                if prop.get("type") == "title":
                    return "".join(item.get("plain_text", "") for item in prop.get("title", [])) or None
        if payload.get("object") == "block":
            block_type = payload.get("type")
            value = payload.get(block_type, {})
            if isinstance(value, dict) and value.get("rich_text"):
                return "".join(item.get("plain_text", "") for item in value["rich_text"]) or None
            if isinstance(value, dict) and value.get("title"):
                return str(value["title"])
        return payload.get("name") or payload.get("title")

    @staticmethod
    def _relation_edges(payload: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
        properties = payload.get("properties")
        if not isinstance(properties, dict):
            return []
        edges: list[tuple[str, str, dict[str, Any]]] = []
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            prop_type = prop.get("type")
            values = prop.get(prop_type, []) if prop_type else []
            if prop_type == "relation" and isinstance(values, list):
                for value in values:
                    if value.get("id"):
                        edges.append(("relation", str(value["id"]), {"property": name}))
        return edges

    @staticmethod
    def _upsert_edge(
        session: Session,
        *,
        root_id: str,
        from_key: str,
        to_key: str,
        edge_type: str,
        position: int | None,
        metadata: dict[str, Any],
    ) -> None:
        if edge_type == "parent":
            old_edges = session.scalars(
                select(GraphEdge).where(
                    GraphEdge.root_id == root_id,
                    GraphEdge.to_object_key == to_key,
                    GraphEdge.edge_type == edge_type,
                    GraphEdge.is_current.is_(True),
                )
            ).all()
            for old_edge in old_edges:
                if old_edge.from_object_key != from_key or old_edge.position != position:
                    old_edge.is_current = False
                    old_edge.ended_at = utc_now()

        existing = session.scalar(
            select(GraphEdge).where(
                GraphEdge.root_id == root_id,
                GraphEdge.from_object_key == from_key,
                GraphEdge.to_object_key == to_key,
                GraphEdge.edge_type == edge_type,
                GraphEdge.position == position,
                GraphEdge.is_current.is_(True),
            )
        )
        if existing:
            existing.metadata_json = metadata
            return
        session.add(
            GraphEdge(
                root_id=root_id,
                from_object_key=from_key,
                to_object_key=to_key,
                edge_type=edge_type,
                position=position,
                metadata_json=metadata,
                is_current=True,
            )
        )

    @staticmethod
    def record_key(object_kind: str, object_id: str) -> str:
        return f"{object_kind}:{object_id}"

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return str(error).replace("NOTION_TOKEN", "[redacted]")[:2000]
