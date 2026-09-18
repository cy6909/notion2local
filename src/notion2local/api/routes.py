from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import EventInbox, GraphEdge, NotionObject, SyncRoot, SyncRun
from ..notion.client import NotionAPIError, NotionClient, NotionNotConfigured
from ..runtime_secrets import delete_secret_file, write_secret_file
from ..sync.tasks import enqueue_task
from ..utils import utc_now
from .schemas import (
    AdminLogin,
    NotionTokenUpdate,
    ObjectDetail,
    ObjectSummary,
    RootCreate,
    RootResponse,
    RunResponse,
)
from .security import (
    ADMIN_SESSION_COOKIE,
    admin_session_value,
    require_setup_token,
    setup_token_is_valid,
)


def build_router() -> APIRouter:
    router = APIRouter()

    def db_session(request: Request):
        with request.app.state.database.session() as session:
            yield session

    @router.post("/api/v1/admin/session")
    def create_admin_session(body: AdminLogin, request: Request) -> JSONResponse:
        if not setup_token_is_valid(request, body.setup_token.get_secret_value()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="valid setup token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            ADMIN_SESSION_COOKIE,
            admin_session_value(request.app.state.settings.setup_token_value),
            max_age=8 * 60 * 60,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return response

    @router.get("/api/v1/admin/session")
    def get_admin_session(request: Request) -> dict[str, bool]:
        return {"authenticated": _admin_session_is_valid(request)}

    @router.delete("/api/v1/admin/session")
    def delete_admin_session() -> Response:
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
        return response

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
            "notion_token_source": settings.notion_token_source,
            "setup_token_configured": bool(settings.setup_token_value),
            "public_base_url_configured": bool(settings.public_base_url),
            "sync_timezone": settings.sync_timezone,
            "reconcile_time": settings.reconcile_time,
            "root_count": root_count,
        }

    @router.put(
        "/api/v1/admin/notion-token",
        dependencies=[Depends(require_setup_token)],
    )
    def update_notion_token(body: NotionTokenUpdate, request: Request) -> dict[str, Any]:
        path = request.app.state.settings.notion_token_file
        if path is None:
            raise HTTPException(status_code=503, detail="runtime secret file is not configured")
        try:
            write_secret_file(path, body.token.get_secret_value())
        except OSError as exc:
            raise HTTPException(status_code=503, detail="runtime secret file is not writable") from exc
        return {
            "saved": True,
            "notion_configured": bool(request.app.state.settings.notion_token_value),
            "notion_token_source": request.app.state.settings.notion_token_source,
        }

    @router.delete(
        "/api/v1/admin/notion-token",
        dependencies=[Depends(require_setup_token)],
    )
    def delete_notion_token(request: Request) -> dict[str, Any]:
        removed = delete_secret_file(request.app.state.settings.notion_token_file)
        return {
            "removed": removed,
            "notion_configured": bool(request.app.state.settings.notion_token_value),
            "notion_token_source": request.app.state.settings.notion_token_source,
        }

    @router.post(
        "/api/v1/admin/notion/test",
        dependencies=[Depends(require_setup_token)],
    )
    def test_notion_token(request: Request) -> dict[str, Any]:
        try:
            with NotionClient(request.app.state.settings) as client:
                client.retrieve_self()
        except NotionNotConfigured as exc:
            raise HTTPException(status_code=409, detail="Notion token is not configured") from exc
        except NotionAPIError as exc:
            if exc.status_code in {401, 403}:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail="Notion rejected the token or the connection lacks access",
                ) from exc
            raise HTTPException(status_code=502, detail="Notion API connection failed") from exc
        return {"ok": True, "message": "Notion connection accepted"}

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

    @router.delete(
        "/api/v1/sync/roots/{root_id}",
        response_model=RootResponse,
        dependencies=[Depends(require_setup_token)],
    )
    def disable_root(root_id: str, session: Session = Depends(db_session)) -> SyncRoot:
        root = session.get(SyncRoot, root_id)
        if root is None:
            raise HTTPException(status_code=404, detail="sync root not found")
        root.status = "disabled"
        session.flush()
        return root

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
        "/api/v1/sync/runs",
        response_model=list[RunResponse],
        dependencies=[Depends(require_setup_token)],
    )
    def list_runs(
        root_id: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        session: Session = Depends(db_session),
    ) -> list[SyncRun]:
        query = select(SyncRun).order_by(SyncRun.created_at.desc()).limit(limit)
        if root_id:
            query = query.where(SyncRun.root_id == root_id)
        return list(session.scalars(query).all())

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


def _admin_session_is_valid(request: Request) -> bool:
    expected = request.app.state.settings.setup_token_value
    supplied = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied, admin_session_value(expected))


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
