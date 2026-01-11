#  __  __  ____ ____    ____            _     _
# |  \/  |/ ___|  _ \  |  _ \ ___  __ _(_)___| |_ _ __ _   _
# | |\/| | |   | |_) | | |_) / _ \/ _` | / __| __| '__| | | |
# | |  | | |___|  __/  |  _ <  __/ (_| | \__ \ |_| |  | |_| |
# |_|  |_|\____|_|     |_| \_\___|\__, |_|___/\__|_|   \__, |
#                                 |___/                |___/
#

from mcp.server import FastMCP
from mcp_engine.manage import DeviceManage


def register_all(mcp: FastMCP, manage: DeviceManage) -> None:
    """
    统一注册入口：只要 import 模块，就会触发 @mcp.tool() 注册。
    """
    from mcp_tools import (
        system, android_ui
    )

    system.bind(mcp)
    android_ui.bind(mcp, manage)


if __name__ == '__main__':
    pass
