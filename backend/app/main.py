"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .jobs import JobManager


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def _data_dir() -> Path:
    """数据目录，可通过环境变量覆盖，方便测试。"""

    configured = os.environ.get("SRT_CUTTER_DATA")
    if configured:
        return Path(configured).expanduser().resolve()
    return PROJECT_ROOT / "data"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化任务管理器。"""

    factory: Callable[[Path], JobManager] = getattr(
        app.state, "manager_factory", JobManager
    )
    app.state.manager = factory(_data_dir())
    yield


def create_app(
    manager_factory: Callable[[Path], JobManager] | None = None,
) -> FastAPI:
    """创建应用实例。

    manager_factory 允许测试注入假模型，生产环境不传即可。
    """

    app = FastAPI(
        title="SRT 视频重排剪辑器",
        version="1.0.0",
        lifespan=lifespan,
    )
    if manager_factory is not None:
        app.state.manager_factory = manager_factory

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8765",
            "http://127.0.0.1:8765",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    else:

        @app.get("/", response_class=HTMLResponse)
        def placeholder() -> str:
            return """
            <!doctype html>
            <html lang="zh-CN">
              <head><meta charset="utf-8"><title>SRT 视频重排剪辑器</title></head>
              <body style="font-family: -apple-system, sans-serif; padding: 40px;">
                <h1>前端还没有构建</h1>
                <p>请在 frontend 目录执行 <code>npm install &amp;&amp; npm run build</code>，
                或直接运行项目根目录的 <code>./start.sh</code>。</p>
              </body>
            </html>
            """

    return app


app = create_app()
