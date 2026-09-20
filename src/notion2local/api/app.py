from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
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
    static_dir = Path(__file__).with_name("static")

    @app.get("/admin", response_class=HTMLResponse)
    def admin() -> HTMLResponse:
        return HTMLResponse(
            (static_dir / "admin.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/admin-assets.css")
    def admin_styles() -> FileResponse:
        return FileResponse(static_dir / "admin.css", media_type="text/css")

    @app.get("/admin-assets.js")
    def admin_scripts() -> FileResponse:
        return FileResponse(static_dir / "admin.js", media_type="text/javascript")

    @app.get("/library", response_class=HTMLResponse)
    def library() -> HTMLResponse:
        return HTMLResponse(
            (static_dir / "library.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/library-assets.css")
    def library_styles() -> FileResponse:
        return FileResponse(static_dir / "library.css", media_type="text/css")

    @app.get("/library-assets.js")
    def library_scripts() -> FileResponse:
        return FileResponse(static_dir / "library.js", media_type="text/javascript")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> str:
        settings = request.app.state.settings
        return f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{escape(settings.app_name)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:3rem auto;padding:0 1rem;color:#172033}}code{{background:#eef2f7;padding:.15rem .35rem;border-radius:.25rem}}.card{{border:1px solid #dbe2ea;border-radius:12px;padding:1.2rem;margin:1rem 0}}</style>
</head><body><h1>Notion2Local</h1><p>只读、本地、可恢复的 Notion 镜像服务。</p>
<div class='card'><strong>本地笔记</strong><p><a href='/library'>打开本地只读阅读器</a></p></div>
<div class='card'><strong>控制台</strong><p><a href='/admin'>打开可视化初始化与管理控制台</a></p></div>
<div class='card'><strong>服务状态</strong><p><a href='/healthz'>healthz</a> · <a href='/readyz'>readyz</a> · <a href='/docs'>API 文档</a></p></div>
<div class='card'><strong>安全边界</strong><p>Token 只在控制台提交到本地运行时 secret 文件，不会回显、写入 Notion 或前端存储。</p></div>
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
