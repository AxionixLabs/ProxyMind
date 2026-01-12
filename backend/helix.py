#  _   _      _ _
# | | | | ___| (_)_  __
# | |_| |/ _ \ | \ \/ /
# |  _  |  __/ | |>  <
# |_| |_|\___|_|_/_/\_\
#

from mcp.server import FastMCP
from engine.manage import DeviceManage
from register import register_all_tools
from utils import const

mcp = FastMCP(
    name=const.APP_DESC,
    website_url=const.APP_URL,
    host="127.0.0.1",
    port=3333,
    json_response=True
)


def main() -> None:
    register_all_tools(mcp, DeviceManage())

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
