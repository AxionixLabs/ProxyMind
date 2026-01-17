#  _   _      _ _
# | | | | ___| (_)_  __
# | |_| |/ _ \ | \ \/ /
# |  _  |  __/ | |>  <
# |_| |_|\___|_|_/_/\_\
#

from pydantic                     import AnyHttpUrl
from mcp.server.fastmcp           import FastMCP
from mcp.server.auth.settings     import AuthSettings
from engine.manage                import DeviceManage
from backend.mcp_core.core_cli    import Cli
from backend.middlewares.mid_auth import HelixTokenVerifier
from backend.utilities            import const
from backend.utilities.pipeline   import Active
from register                     import register_all_tools

cli = Cli()
cmd_lines = cli.parse_cmd
log_level = cmd_lines.level

mcp = FastMCP(
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


def main() -> None:
    Active.active(log_level)

    register_all_tools(mcp, DeviceManage())

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
