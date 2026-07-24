# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from engine.observability import observe_exception
from mind_nova import const


async def fetch_service_exec_env(timeout: float = 1.5) -> dict[str, typing.Any] | None:
    """读取本地服务运行时环境，失败时返回空值。"""
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.get(f"{const.BASE_URL}/api/runtime/exec-env")
            resp.raise_for_status()
            body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        observe_exception("helix.exec_env.failed", exc, level="WARNING")
        return None

    if not isinstance(body, dict) or not body.get("ok"):
        return None

    data = body.get("data")
    return data if isinstance(data, dict) else None


if __name__ == '__main__':
    pass
