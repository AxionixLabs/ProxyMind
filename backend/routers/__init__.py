#  ____             _
# |  _ \ ___  _   _| |_ ___ _ __ ___
# | |_) / _ \| | | | __/ _ \ '__/ __|
# |  _ < (_) | |_| | ||  __/ |  \__ \
# |_| \_\___/ \__,_|\__\___|_|  |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import FastAPI

from .rt_basic import basic_router
from .rt_pref import pref_router


def register_routers(app: FastAPI) -> None:
    app.include_router(basic_router)
    app.include_router(pref_router)


if __name__ == '__main__':
    pass
