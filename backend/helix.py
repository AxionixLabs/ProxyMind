#  _   _      _ _
# | | | | ___| (_)_  __
# | |_| |/ _ \ | \ \/ /
# |  _  |  __/ | |>  <
# |_| |_|\___|_|_/_/\_\
#

from mcp.server                 import FastMCP
from engine.manage              import DeviceManage
from register                   import register_all_tools
from backend.mcp_core.cli       import Cli
from backend.utilities          import const
from backend.utilities.pipeline import Active


mcp = FastMCP(
    name=const.APP_DESC,
    website_url=const.APP_URL,
    host="127.0.0.1",
    port=3333,
    json_response=True
)


def main() -> None:
    cli = Cli()
    cmd_lines = cli.parse_cmd

    Active.active(cmd_lines.level)

    register_all_tools(mcp, DeviceManage())

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
