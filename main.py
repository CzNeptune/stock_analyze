import os
from typing import Annotated

import typer
import uvicorn

from app.core.enums import EnvironmentEnum

cmd = typer.Typer()


@cmd.command(
    name="run",
    help="启动应用服务, 运行 python main.py run --env=dev 不加参数默认dev环境"
)
def run(env: Annotated[
    EnvironmentEnum, typer.Option("--env", help="运行环境 (dev / prod)")
] = EnvironmentEnum.DEV):
    """启动FastAPI服务"""
    try:
        os.environ["ENVIRONMENT"] = env.value
        typer.echo(f"项目启动中... 当前运行环境: {env.value}")

        # 重新加载最新配置
        from app.conf.setting import get_settings
        from app.conf.setting import reload_settings
        reload_settings()
        settings = get_settings()

        # 配置日志系统
        from app.core.logger import setup_logging
        from app.core.logger import log
        setup_logging()
        log.info("start app")

        uvicorn.run(
            app="app.init_app:create_app",
            host=settings.SERVER_HOST,
            port=settings.SERVER_PORT,
            reload=env.value == EnvironmentEnum.DEV.value,
            factory=True,
            log_config=None,
        )
    except Exception:
        raise
    finally:
        from app.core.logger import cleanup_logging
        cleanup_logging()


@cmd.command(
    name="version",
    help="查看项目版本信息"
)
def version():
    from app.conf.setting import settings
    typer.echo(f"{settings.APPNAME} v{settings.VERSION}")


if __name__ == '__main__':
    cmd()
