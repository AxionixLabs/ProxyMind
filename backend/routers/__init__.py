# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import FastAPI

from .rt_basic import basic_router
from .rt_code import code_router
from .rt_idle import idle_router
from .rt_logs import logs_router
from .rt_pref import pref_router


def register_routers(app: FastAPI) -> None:
    app.include_router(basic_router)
    app.include_router(code_router)
    app.include_router(idle_router)
    app.include_router(logs_router)
    app.include_router(pref_router)


if __name__ == '__main__':
    pass
