#  ____            _        ____             _
# | __ )  __ _ ___(_) ___  |  _ \ ___  _   _| |_ ___ _ __
# |  _ \ / _` / __| |/ __| | |_) / _ \| | | | __/ _ \ '__|
# | |_) | (_| \__ \ | (__  |  _ < (_) | |_| | ||  __/ |
# |____/ \__,_|___/_|\___| |_| \_\___/ \__,_|\__\___|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse
from backend.utilities import const

basic_router = APIRouter(tags=["Basic"])


@basic_router.get(path="/", include_in_schema=False)
async def root() -> FileResponse:
    html = Path(__file__).resolve().parent.parent / "web" / "index.html"
    return FileResponse(html, media_type="text/html; charset=utf-8")


@basic_router.get(path="/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {
        "ok"        : True,
        "service"   : f"{const.APP_NAME} mcp",
        "transport" : "streamable-http"
    }


@basic_router.get(path="/ready", include_in_schema=False)
async def ready() -> dict:
    return {
        "ready" : True
    }


if __name__ == '__main__':
    pass
