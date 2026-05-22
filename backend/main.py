"""mynozi — FastAPI 入口。"""

import sys
from pathlib import Path

# 确保 backend 目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from routers import projects, upload, sentences, export, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库。"""
    await init_db()
    print(f"[mynozi] Database initialized")
    yield


app = FastAPI(
    title="mynozi",
    description="智能配音工坊 — 上传音视频，AI 切句，逐句重新配音",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS（局域网访问需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 路由
app.include_router(projects.router)
app.include_router(upload.router)
app.include_router(sentences.router)
app.include_router(export.router)
app.include_router(settings.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "mynozi"}


# 挂载前端静态文件（生产模式），排除 API 路径避免遮盖
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if frontend_dist.exists():
    import os
    from starlette.responses import FileResponse

    # 先挂载 assets 等静态资源
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # 对 favicon 等资源直接返回
    @app.get("/favicon.svg")
    async def favicon():
        fav = frontend_dist / "favicon.svg"
        if fav.exists():
            return FileResponse(str(fav))
        return FileResponse(str(frontend_dist / "favicon.ico")) if (frontend_dist / "favicon.ico").exists() else None

    # SPA fallback：非 API 路径都返回 index.html
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA fallback — 非 API 路径返回 index.html 交给前端路由。"""
        # 不走 API 路径
        if full_path.startswith("api/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        file_path = frontend_dist / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(frontend_dist / "index.html"))

    print(f"[mynozi] Serving frontend from {frontend_dist}")


if __name__ == "__main__":
    import uvicorn
    from config import settings

    print(f"[mynozi] Starting on http://{settings.host}:{settings.port}")
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
