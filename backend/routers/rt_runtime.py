# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import APIRouter
from backend.utilities.runtime.exec_env import exec_env

runtime_router = APIRouter(tags=["Runtime"])


@runtime_router.get(path="/api/runtime/exec-env", include_in_schema=False)
async def api_exec_env() -> dict:
    """
    返回 Helix 进程的本地执行环境信息。

    请求参数:
        无。

    返回:
        {
          "ok": true,
          "data": {
            "platform": {...},
            "shell": {...},
            "runtimes": {...},
            "tools": {...},
            "env": {...},
            "workspace": {...}
          }
        }
    """
    return {
        "ok"   : True,
        "data" : exec_env()
    }


if __name__ == '__main__':
    pass
