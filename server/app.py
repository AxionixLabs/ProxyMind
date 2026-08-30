# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from infrastructure.config.session import ConfigSession
from .page import web_dir
from .routers import register_routers


def create_app(config_session: ConfigSession) -> FastAPI:
    """创建配置服务应用。"""
    app = FastAPI()
    app.state.agent_example = None
    app.state.config_session = config_session

    register_routers(app)

    static_dir = web_dir() / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


if __name__ == "__main__":
    pass
