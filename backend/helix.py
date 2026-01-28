#  _   _      _ _
# | | | | ___| (_)_  __
# | |_| |/ _ \ | \ \/ /
# |  _  |  __/ | |>  <
# |_| |_|\___|_|_/_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import uvicorn
import contextlib
from pathlib import Path
from pydantic import AnyHttpUrl
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from backend.mcp_core.core_cli import Cli
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_auth import HelixTokenVerifier
from backend.middlewares.mid_touch import touch_middleware
from routers.rt_basic import basic_router
from backend.utilities import const
from backend.utilities.pipeline import (
    Active, Idle
)
from register import register_all_tools


cli = Cli()
cmd_lines = cli.parse_cmd
log_level = cmd_lines.level

mcp: FastMCP = FastMCP(
    name=const.APP_DESC,
    website_url=const.APP_URL,
    host="127.0.0.1",
    port=3333,
    log_level=log_level,
    json_response=True,
    token_verifier=HelixTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(const.ISSUER),
        resource_server_url=AnyHttpUrl(const.RS_URL),
        required_scopes=["user"]
    )
)

idle: Idle = Idle(ttl_sec=300.0)


@contextlib.asynccontextmanager
async def lifespan(web_app: FastAPI) -> typing.AsyncGenerator[None, None]:
    web_app.mount(
        path=f"/{const.APP_NAME}", app=mcp.streamable_http_app()
    )
    directory = Path(__file__).resolve().parent / "web" / "static"
    web_app.mount(
        path=f"/static", app=StaticFiles(directory=directory)
    )
    async with mcp.session_manager.run():
        await idle.start_idle()
        yield
        await idle.close_idle()


def main() -> None:
    Active.active(log_level)

    register_all_tools(mcp, DeviceManage(), idle)

    app: FastAPI = FastAPI(lifespan=lifespan)
    app.middleware("http")(touch_middleware(idle))
    app.include_router(basic_router)

    uvicorn.run(app, host="127.0.0.1", port=3333, log_level=log_level.lower())


if __name__ == "__main__":
    main()
