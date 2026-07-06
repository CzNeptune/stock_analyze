from collections.abc import AsyncGenerator
from typing import Any

from fastapi import FastAPI
from fastapi.concurrency import asynccontextmanager
from fastapi.staticfiles import StaticFiles

from app.conf.path import STATIC_DIR
from app.core.logger import log


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[Any, Any]:
    """
    应用生命周期管理
    """
    log.info("when app starting")
    yield
    log.info("when app ending")


def register_routers(app: FastAPI):
    """注册路由"""
    from app.routers.v1.stock import router as stock_router
    app.include_router(stock_router)

    from app.routers.view.stock import router as stock_viewer
    app.include_router(stock_viewer)

    app.mount("/stock-static", StaticFiles(directory=STATIC_DIR), name="stock-static")


def create_app() -> FastAPI:
    """创建FastAPI应用实例"""

    app = FastAPI(lifespan=lifespan)

    # 配置日志系统
    from app.core.logger import setup_logging
    setup_logging()

    # 注册路由
    register_routers(app)
    return app
