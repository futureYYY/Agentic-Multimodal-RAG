"""
FastAPI 应用入口
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from contextlib import asynccontextmanager
import os

from app.core.config import get_settings, ensure_directories
from app.core.database import init_db
from app.api import api_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    ensure_directories()
    init_db()
    print("数据库初始化完成")

    # 调试：打印所有路由
    print("🚀 [Startup] Registered Routes:")
    for route in app.routes:
        if isinstance(route, APIRoute):
            print(f"   {route.methods} {route.path}")

    yield
    # 关闭时执行
    print("应用关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="Local Multimodal Agent RAG API",
    description="本地化多模态 Agent RAG 系统接口",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(api_router)

# 调试：中间件记录请求
@app.middleware("http")
async def log_requests(request: Request, call_next):
    import sys
    # 强制刷新缓冲区
    sys.stdout.flush()
    print(f"📥 [Request] {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        print(f"📤 [Response] {response.status_code} {request.url.path}")
        sys.stdout.flush()
        return response
    except Exception as e:
        print(f"❌ [Error] {request.url.path}: {str(e)}")
        sys.stdout.flush()
        raise e

# 静态文件服务（图片预览）
if os.path.exists(settings.IMAGE_DIR):
    app.mount(
        "/static/images",
        StaticFiles(directory=settings.IMAGE_DIR),
        name="images",
    )


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器"""
    return JSONResponse(
        status_code=500,
        content={
            "code": 50000,
            "message": f"服务器内部错误: {str(exc)}",
        },
    )


# 健康检查
@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok"}


# 根路径
@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "Local Multimodal Agent RAG API",
        "version": "1.0.0",
        "docs": "/docs",
    }
