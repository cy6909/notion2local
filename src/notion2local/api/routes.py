from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import EventInbox, GraphEdge, NotionObject, SyncRoot, SyncRun
from ..sync.tasks import enqueue_task
from ..utils import utc_now
from .schemas import ObjectDetail, ObjectSummary, RootCreate, RootResponse, RunResponse
from .security import require_setup_token


def build_router() -> APIRouter:
    router = APIRouter()

    def db_session(request: Request):
        with request.app.state.database.session() as session:
            yield session

    @router.get("/api/v1/setup/status")
    def setup_status(request: Request, session: Session = Depends(db_session)) -> dict[str, Any]:
        root_count = session.scalar(select(func.count()).select_from(SyncRoot)) or 0
        settings = request.app.state.settings
        if not settings.notion_token_value:
            state = "needs_notion_credential"
        elif root_count == 0:
            state = "needs_root_scope"
        else:
            state = "ready"
        return {
            "state": state,
            "service": settings.app_name,
            "api_version": settings.notion_api_version,
            "notion_configured": bool(settings.notion_token_value),
            "setup_token_configured": bool(settings.setup_token_value),
            "public_base_url_configured": bool(settings.public_base_url),
            "sync_timezone": settings.sync_timezone,
            "reconcile_time": settings.reconcile_time,
            "root_count": root_count,
        }

    @router.post(
        "/api/v1/sync/roots",
        response_model=RootResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_setup_token)],
    )
    def create_root(body: RootCreate, session: Session = Depends(db_session)) -> SyncRoot:
        existing = session.scalar(
            select(SyncRoot).where(
                SyncRoot.root_object_id == body.root_object_id,
                SyncRoot.status != "disabled",
            )
        )
        if existing:
            raise HTTPException(status_code=409, detail="this root page is already configured")
        root = SyncRoot(
            name=body.name,
            root_object_id=body.root_object_id,
            status="configured",
            strategy=body.strategy,
            timezone=body.timezone,
            include_comments=body.include_comments,
            include_assets=body.include_assets,
        )
        session.add(root)
        session.flush()
        return root

    @router.get(
        "/api/v1/sync/roots",
        response_model=list[RootResponse],
        dependencies=[Depends(require_setup_token)],
    )
    def list_roots(session: Session = Depends(db_session)) -> list[SyncRoot]:
        return list(session.scalars(select(SyncRoot).order_by(SyncRoot.created_at)).all())

    @router.post(
        "/api/v1/sync/roots/{root_id}/runs",
        response_model=RunResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_setup_token)],
    )
    def enqueue_root_run(root_id: str, session: Session = Depends(db_session)) -> SyncRun:
        root = session.get(SyncRoot, root_id)
        if root is None:
            raise HTTPException(status_code=404, detail="sync root not found")
        run = SyncRun(root_id=root.id, kind="manual", status="queued", stats_json={})
        session.add(run)
        session.flush()
        enqueue_task(
            session,
            root_id=root.id,
            task_type="root_sync",
            idempotency_key=f"manual:{run.id}",
            payload={"kind": "manual", "run_id": run.id},
        )
        return run

    @router.get(
        "/api/v1/sync/runs/{run_id}",
        response_model=RunResponse,
        dependencies=[Depends(require_setup_token)],
    )
    def get_run(run_id: str, session: Session = Depends(db_session)) -> SyncRun:
        run = session.get(SyncRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="sync run not found")
        return run

    @router.get(
        "/api/v1/library",
        response_model=list[ObjectSummary],
        dependencies=[Depends(require_setup_token)],
    )
    def list_library(
        session: Session = Depends(db_session),
        root_id: str | None = None,
        object_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[NotionObject]:
        query = select(NotionObject).order_by(NotionObject.updated_at.desc()).offset(offset).limit(limit)
        if root_id:
            query = query.where(NotionObject.root_id == root_id)
        if object_type:
            query = query.where(NotionObject.object_type == object_type)
        return list(session.scalars(query).all())

    @router.get(
        "/api/v1/library/objects/{object_id}",
        response_model=ObjectDetail,
        dependencies=[Depends(require_setup_token)],
    )
    def get_object(
        object_id: str,
        session: Session = Depends(db_session),
        object_type: str = "page",
    ) -> ObjectDetail:
        record_key = f"{object_type}:{object_id}"
        current = session.get(NotionObject, record_key)
        if current is None:
            raise HTTPException(status_code=404, detail="object not found")
        edges = session.scalars(
            select(GraphEdge).where(
                GraphEdge.is_current.is_(True),
                (GraphEdge.from_object_key == record_key) | (GraphEdge.to_object_key == record_key),
            )
        ).all()
        return ObjectDetail(
            **ObjectSummary.model_validate(current).model_dump(),
            current_payload=current.current_payload,
            current_raw_path=current.current_raw_path,
            in_trash=current.in_trash,
            archived=current.archived,
            has_children=current.has_children,
            edges=[
                {
                    "from": edge.from_object_key,
                    "to": edge.to_object_key,
                    "type": edge.edge_type,
                    "position": edge.position,
                    "metadata": edge.metadata_json,
                }
                for edge in edges
            ],
        )

    @router.post("/webhooks/notion")
    async def notion_webhook(request: Request) -> Response:
        body = await request.body()
        secret = request.app.state.settings.notion_webhook_secret_value
        if not secret:
            raise HTTPException(status_code=503, detail="Notion webhook secret is not configured")
        signature = request.headers.get("X-Notion-Signature") or request.headers.get(
            "X-Notion-Webhook-Signature"
        )
        if not signature or not _verify_signature(body, signature, secret):
            raise HTTPException(status_code=401, detail="invalid webhook signature")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON payload") from exc

        if payload.get("verification_token"):
            return Response(
                content=json.dumps({"verification_token": payload["verification_token"]}),
                media_type="application/json",
            )

        event_id = str(payload.get("id") or payload.get("event_id") or hashlib.sha256(body).hexdigest())
        event_type = str(payload.get("type") or payload.get("event_type") or "unknown")
        object_id = _event_object_id(payload)
        object_kind = _event_object_kind(payload, event_type)
        with request.app.state.database.session() as session:
            if session.get(EventInbox, event_id):
                return Response(status_code=202)
            session.add(
                EventInbox(
                    event_id=event_id,
                    event_type=event_type,
                    object_id=object_id,
                    payload=payload,
                )
            )
            roots = list(session.scalars(select(SyncRoot).where(SyncRoot.status != "disabled")).all())
            for root in roots:
                if object_id and object_kind in {"page", "block", "database", "data_source"}:
                    enqueue_task(
                        session,
                        root_id=root.id,
                        task_type="object_sync",
                        object_id=object_id,
                        idempotency_key=f"event:{event_id}:{root.id}",
                        payload={"object_kind": object_kind, "event_type": event_type},
                    )
                else:
                    enqueue_task(
                        session,
                        root_id=root.id,
                        task_type="root_sync",
                        idempotency_key=f"event-reconcile:{event_id}:{root.id}",
                        payload={"kind": "webhook_reconcile"},
                    )
            event = session.get(EventInbox, event_id)
            if event:
                event.processed_at = utc_now()
        return Response(status_code=202)

    return router


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    supplied = signature.removeprefix("sha256=").strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def _event_object_id(payload: dict[str, Any]) -> str | None:
    for candidate in (
        payload.get("object_id"),
        (payload.get("data") or {}).get("object", {}).get("id")
        if isinstance(payload.get("data"), dict)
        and isinstance((payload.get("data") or {}).get("object"), dict)
        else None,
        (payload.get("entity") or {}).get("id")
        if isinstance(payload.get("entity"), dict)
        else None,
    ):
        if candidate:
            return str(candidate)
    return None


def _event_object_kind(payload: dict[str, Any], event_type: str) -> str:
    data = payload.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if isinstance(obj, dict):
        kind = obj.get("object") or obj.get("type")
        if kind in {"page", "block", "database", "data_source"}:
            return str(kind)
    prefix = event_type.split(".", 1)[0]
    return prefix if prefix in {"page", "block", "database", "data_source"} else "unknown"
