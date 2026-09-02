# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from fastapi import (
    APIRouter,
    HTTPException,
    Request
)
from fastapi.responses import Response

from infrastructure.config.session import ConfigSession
from ..page import render_page
from ..storage import (
    create_provider,
    delete_provider,
    load_pref,
    save_pref,
    set_active_provider,
    update_provider
)

pref_router = APIRouter(tags=["Pref"])


@pref_router.get(path="/pref", include_in_schema=False)
async def api_pref_page() -> Response:
    """返回偏好设置页面。"""
    return render_page("pref.html")


@pref_router.get(path="/api/pref", include_in_schema=False)
async def api_pref_load(request: Request) -> dict[str, typing.Any]:
    """读取偏好配置。"""
    config_session: ConfigSession = request.app.state.config_session
    return {
        "ok": True,
        "data": load_pref(config_session)
    }


@pref_router.put(path="/api/pref", include_in_schema=False)
async def api_pref_save(request: Request) -> dict[str, typing.Any]:
    """保存偏好配置。"""
    payload = await request.json()
    config_session: ConfigSession = request.app.state.config_session

    return {
        "ok": True,
        "data": save_pref(config_session, payload)
    }


@pref_router.post(path="/api/pref/providers", include_in_schema=False)
async def api_provider_create(request: Request) -> dict[str, typing.Any]:
    """创建 Provider Profile。"""
    payload = await request.json()
    config_session: ConfigSession = request.app.state.config_session
    return _provider_response(create_provider, config_session, payload)


@pref_router.patch(path="/api/pref/providers/{provider_id}", include_in_schema=False)
async def api_provider_update(
    provider_id: str,
    request: Request,
) -> dict[str, typing.Any]:
    """更新 Provider Profile。"""
    payload = await request.json()
    config_session: ConfigSession = request.app.state.config_session
    return _provider_response(update_provider, config_session, provider_id, payload)


@pref_router.delete(path="/api/pref/providers/{provider_id}", include_in_schema=False)
async def api_provider_delete(
    provider_id: str,
    request: Request
) -> dict[str, typing.Any]:
    """删除 Provider Profile。"""
    config_session: ConfigSession = request.app.state.config_session
    return _provider_response(delete_provider, config_session, provider_id)


@pref_router.put(path="/api/pref/active-provider", include_in_schema=False)
async def api_provider_activate(request: Request) -> dict[str, typing.Any]:
    """切换当前 Provider Profile。"""
    payload = await request.json()
    data = payload if isinstance(payload, dict) else {}

    config_session: ConfigSession = request.app.state.config_session

    return _provider_response(
        set_active_provider,
        config_session,
        data.get("provider"),
    )


def _provider_response(
    action: typing.Callable[..., dict[str, typing.Any]],
    *args: typing.Any
) -> dict[str, typing.Any]:
    """执行 Provider 配置操作并转换校验错误。"""
    try:
        data = action(*args)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"ok": True, "data": data}


if __name__ == "__main__":
    pass
