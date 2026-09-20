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
from ..sync.service import WORKSPACE_ROOT_ID
from ..sync.tasks import enqueue_task
from ..utils import utc_now
from .schemas import (
    AdminLogin,
    LibraryBlock,
    LibraryPageContent,
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
        workspace_root = session.scalar(
            select(SyncRoot).where(
                SyncRoot.root_object_id == WORKSPACE_ROOT_ID,
                SyncRoot.status != "disabled",
            )
        )
        workspace_configured = workspace_root is not None
        workspace_initialized = bool(workspace_root and workspace_root.last_sync_at is not None)
        settings = request.app.state.settings
        if not settings.notion_token_value:
            state = "needs_notion_credential"
        elif not workspace_initialized:
            state = "needs_workspace_initialization"
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
            "workspace_configured": workspace_configured,
            "workspace_initialized": workspace_initialized,
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
        "/api/v1/sync/workspace",
        response_model=RunResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_setup_token)],
    )
    def enqueue_workspace_sync(request: Request, session: Session = Depends(db_session)) -> SyncRun:
        """Initialize or reconcile every object visible to the Notion connection."""

        if not request.app.state.settings.notion_token_value:
            raise HTTPException(status_code=409, detail="Notion token is not configured")
        root = session.scalar(select(SyncRoot).where(SyncRoot.root_object_id == WORKSPACE_ROOT_ID))
        if root is None:
            root = SyncRoot(
                name="全工作区（自动发现）",
                root_object_id=WORKSPACE_ROOT_ID,
                status="configured",
                strategy="deep",
                timezone="Asia/Shanghai",
                include_comments=True,
                include_assets=True,
            )
            session.add(root)
            session.flush()
        elif root.status == "disabled":
            root.status = "configured"

        active_run = session.scalar(
            select(SyncRun)
            .where(
                SyncRun.root_id == root.id,
                SyncRun.status.in_({"queued", "running"}),
            )
            .order_by(SyncRun.created_at.desc())
        )
        if active_run:
            return active_run

        kind = "initial" if root.last_sync_at is None else "reconcile"
        run = SyncRun(root_id=root.id, kind=kind, status="queued", stats_json={})
        session.add(run)
        session.flush()
        enqueue_task(
            session,
            root_id=root.id,
            task_type="root_sync",
            idempotency_key=f"workspace:{root.id}:{run.id}",
            payload={"kind": kind, "run_id": run.id},
        )
        return run

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
        "/api/v1/library/stats",
        dependencies=[Depends(require_setup_token)],
    )
    def library_stats(
        session: Session = Depends(db_session),
        root_id: str | None = None,
    ) -> dict[str, Any]:
        """Return non-sensitive local archive counts and the latest run checkpoint."""

        root = session.scalar(select(SyncRoot).where(SyncRoot.root_object_id == WORKSPACE_ROOT_ID))
        effective_root_id = root_id or (root.id if root else None)
        counts: dict[str, int] = {}
        latest_run: SyncRun | None = None
        if effective_root_id:
            rows = session.execute(
                select(NotionObject.object_type, func.count())
                .where(NotionObject.root_id == effective_root_id)
                .group_by(NotionObject.object_type)
            ).all()
            counts = {str(object_type): int(count) for object_type, count in rows}
            latest_run = session.scalar(
                select(SyncRun)
                .where(SyncRun.root_id == effective_root_id)
                .order_by(SyncRun.created_at.desc())
            )
        return {
            "root_id": effective_root_id,
            "total": sum(counts.values()),
            "counts": counts,
            "workspace_initialized": bool(root and root.last_sync_at is not None),
            "root_status": root.status if root else None,
            "run": RunResponse.model_validate(latest_run).model_dump(mode="json") if latest_run else None,
        }

    @router.get(
        "/api/v1/library/pages/{page_id}/content",
        response_model=LibraryPageContent,
        dependencies=[Depends(require_setup_token)],
    )
    def get_page_content(
        page_id: str,
        depth: int = Query(default=4, ge=1, le=10),
        limit: int = Query(default=600, ge=1, le=2000),
        session: Session = Depends(db_session),
    ) -> LibraryPageContent:
        """Return a bounded, ordered local projection of a page and its block tree."""

        page = session.get(NotionObject, f"page:{page_id}")
        if page is None:
            raise HTTPException(status_code=404, detail="page not found")

        current_edges = list(
            session.scalars(
                select(GraphEdge).where(
                    GraphEdge.root_id == page.root_id,
                    GraphEdge.edge_type == "parent",
                    GraphEdge.is_current.is_(True),
                )
            ).all()
        )
        children_by_parent: dict[str, list[GraphEdge]] = {}
        for edge in current_edges:
            children_by_parent.setdefault(edge.from_object_key, []).append(edge)
        for edges in children_by_parent.values():
            edges.sort(key=lambda edge: (edge.position is None, edge.position or 0, edge.created_at))

        pending: list[tuple[str, int]] = [(page.record_key, 0)]
        visited = {page.record_key}
        discovered: list[tuple[GraphEdge, int]] = []
        truncated = False
        while pending:
            parent_key, parent_depth = pending.pop(0)
            if parent_depth >= depth:
                continue
            for edge in children_by_parent.get(parent_key, []):
                if edge.to_object_key in visited:
                    continue
                visited.add(edge.to_object_key)
                child_depth = parent_depth + 1
                discovered.append((edge, child_depth))
                if len(discovered) >= limit:
                    truncated = True
                    break
                pending.append((edge.to_object_key, child_depth))
            if truncated:
                break

        object_keys = [edge.to_object_key for edge, _ in discovered]
        objects = {
            item.record_key: item
            for item in session.scalars(
                select(NotionObject).where(NotionObject.record_key.in_(object_keys))
            ).all()
        }
        blocks = [
            LibraryBlock(
                record_key=edge.to_object_key,
                object_id=objects[edge.to_object_key].object_id,
                object_type=objects[edge.to_object_key].object_type,
                parent_object_id=objects[edge.to_object_key].parent_object_id,
                title=objects[edge.to_object_key].title,
                sync_state=objects[edge.to_object_key].sync_state,
                position=edge.position,
                depth=child_depth,
                current_payload=objects[edge.to_object_key].current_payload,
                in_trash=objects[edge.to_object_key].in_trash,
                archived=objects[edge.to_object_key].archived,
                has_children=objects[edge.to_object_key].has_children,
            )
            for edge, child_depth in discovered
            if edge.to_object_key in objects
        ]
        return LibraryPageContent(page=_object_detail(session, page), blocks=blocks, truncated=truncated)

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
        return _object_detail(session, current)

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


def _object_detail(session: Session, current: NotionObject) -> ObjectDetail:
    record_key = current.record_key
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
