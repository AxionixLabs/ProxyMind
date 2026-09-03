# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from fastapi import FastAPI

from .agent import agent_router
from .basic import basic_router
from .pref import pref_router
from .services import service_router


def register_routers(app: FastAPI) -> None:
    """注册配置服务路由。"""
    app.include_router(basic_router)
    app.include_router(agent_router)
    app.include_router(pref_router)
    app.include_router(service_router)
