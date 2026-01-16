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
        app_control,
        device_info,
        file_control,
        media_control,
        system_control,
        ui_interaction,
        zest
    )

    app_control.bind(mcp, manage)
    device_info.bind(mcp, manage)
    file_control.bind(mcp, manage)
    media_control.bind(mcp, manage)
    system_control.bind(mcp, manage)
    ui_interaction.bind(mcp, manage)
    zest.bind(mcp)


if __name__ == '__main__':
    pass
