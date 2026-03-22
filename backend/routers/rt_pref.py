#  ___    _ _        ____             _
# |_ _|__| | | ___  |  _ \ ___  _   _| |_ ___ _ __
#  | |/ _` | |/ _ \ | |_) / _ \| | | | __/ _ \ '__|
#  | | (_| | |  __/ |  _ < (_) | |_| | ||  __/ |
# |___\__,_|_|\___| |_| \_\___/ \__,_|\__\___|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from pathlib import Path
from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from backend.utilities import const
from backend.utilities.storage.prefs import (
    load_pref, save_pref
)

pref_router = APIRouter(tags=["Pref"])


@pref_router.get(path="/pref", include_in_schema=False)
async def api_pref_page() -> Response:
    html = Path(__file__).resolve().parent.parent / "web" / "pref.html"
    html = html.read_text(encoding=const.CHARSET, errors="replace")
    html = html.replace("__APP_VERSION__", const.APP_VERSION)
    return Response(html, media_type="text/html; charset=utf-8")


@pref_router.get(path="/api/pref", include_in_schema=False)
async def api_pref_load() -> dict:
    return {
        "ok"   : True,
        "data" : load_pref()
    }


@pref_router.put(path="/api/pref", include_in_schema=False)
async def api_pref_save(request: Request) -> dict:
    payload = await request.json()
    return {
        "ok"   : True,
        "data" : save_pref(payload)
    }


if __name__ == '__main__':
    pass
