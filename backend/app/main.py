from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.api import admin, auth, documents, knowledge_bases, users
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.db.session import init_db
from app.services.user_service import avatar_dir


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
register_exception_handlers(app)

api = APIRouter(prefix="/api")
api.include_router(auth.router)
api.include_router(users.router)
api.include_router(admin.router)
api.include_router(knowledge_bases.router)
api.include_router(documents.router)
app.include_router(api)


@app.middleware("http")
async def no_cache_frontend(request: Request, call_next):
    """前端 JS/CSS 无构建、无文件指纹：要求浏览器每次用 ETag 校验，避免更新后仍运行旧代码。"""
    response = await call_next(request)
    if not request.url.path.startswith(("/api", "/uploads")):
        response.headers["Cache-Control"] = "no-cache"
    return response


avatar_dir()  # 确保上传目录存在
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")
# 前端静态页面挂在根路径，必须放在最后，避免覆盖 /api 路由
app.mount("/", StaticFiles(directory=settings.FRONTEND_DIR, html=True), name="frontend")
