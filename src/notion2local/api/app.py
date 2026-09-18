from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from .. import __version__
from ..config import Settings, get_settings
from ..db import Database
from .routes import build_router


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    app_database = database or Database(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        app.state.database = app_database
        app_settings.raw_storage_path.mkdir(parents=True, exist_ok=True)
        app_settings.blob_storage_path.mkdir(parents=True, exist_ok=True)
        if app_settings.db_auto_create:
            app_database.create_all()
        yield
        app_database.dispose()

    app = FastAPI(title="Notion2Local", version=__version__, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.database = app_database
    app.include_router(build_router())

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> str:
        settings = request.app.state.settings
        return f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{escape(settings.app_name)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:3rem auto;padding:0 1rem;color:#172033}}code{{background:#eef2f7;padding:.15rem .35rem;border-radius:.25rem}}.card{{border:1px solid #dbe2ea;border-radius:12px;padding:1.2rem;margin:1rem 0}}</style>
</head><body><h1>Notion2Local</h1><p>只读、本地、可恢复的 Notion 镜像服务。</p>
<div class='card'><strong>服务状态</strong><p><a href='/healthz'>healthz</a> · <a href='/readyz'>readyz</a> · <a href='/docs'>API 文档</a></p></div>
<div class='card'><strong>下一步</strong><p>通过 <code>/api/v1/setup/status</code> 查看非敏感配置状态；管理接口需要配置并携带 setup token。</p></div>
</body></html>"""

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, str]:
        return {"status": "ok", "service": request.app.state.settings.app_name, "version": __version__}

    @app.get("/readyz")
    def readyz(request: Request) -> dict[str, str]:
        try:
            with request.app.state.database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="database is not ready") from exc
        return {"status": "ready"}

    return app
