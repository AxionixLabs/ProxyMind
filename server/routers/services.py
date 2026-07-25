# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from fastapi import APIRouter, Request
from mind_core.config_session import ConfigSession
from ..storage import (
    load_service_config,
    save_service_config
)

service_router = APIRouter(tags=["ServiceConfig"])


@service_router.get(path="/api/service-config", include_in_schema=False)
async def api_service_config_load(request: Request) -> dict[str, typing.Any]:
    """读取服务域名配置。"""
    config_session: ConfigSession = request.app.state.config_session

    return {
        "ok"   : True,
        "data" : load_service_config(config_session)
    }


@service_router.put(path="/api/service-config", include_in_schema=False)
async def api_service_config_save(request: Request) -> dict[str, typing.Any]:
    """保存服务域名配置。"""
    payload = await request.json()

    config_session: ConfigSession = request.app.state.config_session

    return {
        "ok"   : True,
        "data" : save_service_config(config_session, payload)
    }


if __name__ == "__main__":
    pass
