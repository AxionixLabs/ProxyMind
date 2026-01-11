#  __  __  ____ ____    ____
# |  \/  |/ ___|  _ \  / ___|  ___ _ ____   _____ _ __
# | |\/| | |   | |_) | \___ \ / _ \ '__\ \ / / _ \ '__|
# | |  | | |___|  __/   ___) |  __/ |   \ V /  __/ |
# |_|  |_|\____|_|     |____/ \___|_|    \_/ \___|_|
#

from mcp.server import FastMCP
from mcp_engine.manage import DeviceManage
from mcp_registry import register_all
from utils import const

mcp = FastMCP(
    name=const.APP_DESC,
    website_url=const.APP_URL,
    host="127.0.0.1",
    port=3333,
    json_response=True
)


def main() -> None:
    register_all(mcp, DeviceManage())

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
