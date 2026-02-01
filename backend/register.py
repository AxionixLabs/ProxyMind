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
from backend.utilities.pipeline import Idle


def register_automator_tools(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:
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
    ui_interaction.bind(mcp, manage, idle)
    zest.bind(mcp, manage, idle)


def register_bench_tools(mcp: FastMCP, idle: Idle) -> None:
    from backend.mcp_tools.bench import bench_framix
    from backend.mcp_tools.bench import bench_memrix

    bench_framix.bind(mcp, idle)
    bench_memrix.bind(mcp, idle)


def register_capture_tools(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:
    from backend.mcp_tools.capture import screen_record

    screen_record.bind(mcp, manage, idle)


def register_common_tools(mcp: FastMCP, idle: Idle) -> None:
    from backend.mcp_tools.common import information

    information.bind(mcp, idle)


def register_media_tools(mcp: FastMCP, idle: Idle) -> None:
    from backend.mcp_tools.media import audio
    from backend.mcp_tools.media import ffmpeg

    audio.bind(mcp, idle)
    ffmpeg.bind(mcp, idle)


def register_performance_tools() -> None:
    pass


def register_all_tools(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:
    register_automator_tools(mcp, manage, idle)
    register_bench_tools(mcp, idle)
    register_capture_tools(mcp, manage, idle)
    register_common_tools(mcp, idle)
    register_media_tools(mcp, idle)
    register_performance_tools()


if __name__ == '__main__':
    pass
