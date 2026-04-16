# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import uvicorn
import contextlib
from loguru import logger
from pydantic import AnyHttpUrl
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from backend.mcp_core.core_cli import Cli
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_auth import HelixTokenVerifier
from backend.middlewares import register_middlewares
from backend.routers import register_routers
from backend.utilities.paths import resource_path
from backend.utilities.runtime import (
    app_ctx, Active, Idle
)
from backend.utilities import const
from backend.register import register_all_tools


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

idle: Idle = Idle(
    ttl_sec=const.IDLE_TTL_SEC,
    snapshot_provider=app_ctx.instance_snapshots
)


@contextlib.asynccontextmanager
async def lifespan(web_app: FastAPI) -> typing.AsyncGenerator[None, None]:
    logger.debug("mounting MCP and static assets")
    web_app.mount(
        path=f"/{const.APP_NAME}", app=mcp.streamable_http_app()
    )
    directory = resource_path("web", "static")
    web_app.mount(
        path=f"/static", app=StaticFiles(directory=directory)
    )

    web_app.state.idle = idle
    web_app.state.ctx = app_ctx
    web_app.state.agent_example = None

    logger.debug("session manager and idle loop starting")
    async with mcp.session_manager.run():
        await idle.start_idle()
        try:
            logger.info("runtime ready")
            yield
        finally:
            logger.info("runtime shutting down")
            await idle.close_idle()


def main() -> None:
    Active.active(log_level)
    logger.info(f"boot begin level={log_level} host=127.0.0.1 port=3333")

    register_all_tools(mcp, DeviceManage(), idle, app_ctx)
    logger.info("tools registered, web app starting")

    app: FastAPI = FastAPI(lifespan=lifespan)
    register_middlewares(app)
    register_routers(app)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=3333,
        log_level=log_level.lower()
    )


if __name__ == "__main__":
    main()
