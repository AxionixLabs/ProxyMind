#  ____            _     _
# |  _ \ ___  __ _(_)___| |_ ___ _ __
# | |_) / _ \/ _` | / __| __/ _ \ '__|
# |  _ <  __/ (_| | \__ \ ||  __/ |
# |_| \_\___|\__, |_|___/\__\___|_|
#            |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server.fastmcp         import FastMCP
from backend.mcp_hub.hub_manage import DeviceManage


def register_all_tools(mcp: FastMCP, manage: DeviceManage) -> None:
    from backend.mcp_tools.automator import app_control
    from backend.mcp_tools.automator import device_info
    from backend.mcp_tools.automator import file_control
    from backend.mcp_tools.automator import media_control
    from backend.mcp_tools.automator import system_control
    from backend.mcp_tools.automator import ui_interaction
    from backend.mcp_tools.automator import zest

    app_control.bind(mcp, manage)
    device_info.bind(mcp, manage)
    file_control.bind(mcp, manage)
    media_control.bind(mcp, manage)
    system_control.bind(mcp, manage)
    ui_interaction.bind(mcp, manage)
    zest.bind(mcp, manage)

    from backend.mcp_tools.performance import monitor

    monitor.bind(mcp, manage)


if __name__ == '__main__':
    pass
