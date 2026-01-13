#  ____            _     _
# |  _ \ ___  __ _(_)___| |_ ___ _ __
# | |_) / _ \/ _` | / __| __/ _ \ '__|
# |  _ <  __/ (_| | \__ \ ||  __/ |
# |_| \_\___|\__, |_|___/\__\___|_|
#            |___/
#

from mcp.server    import FastMCP
from engine.manage import DeviceManage


def register_all_tools(mcp: FastMCP, manage: DeviceManage) -> None:
    from mcp_tools import (
        ui, toolbox
    )

    ui.bind(mcp, manage)
    toolbox.bind(mcp)


if __name__ == '__main__':
    pass
