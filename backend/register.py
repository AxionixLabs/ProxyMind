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
    from backend.mcp_tools.automator import ctl_app
    from backend.mcp_tools.automator import ctl_file
    from backend.mcp_tools.automator import ctl_info
    from backend.mcp_tools.automator import ctl_keyevent
    from backend.mcp_tools.automator import ctl_system
    from backend.mcp_tools.automator import ctl_ui
    from backend.mcp_tools.automator import ctl_zest

    ctl_app.bind(mcp, manage)
    ctl_file.bind(mcp, manage)
    ctl_info.bind(mcp, manage)
    ctl_keyevent.bind(mcp, manage)
    ctl_system.bind(mcp, manage)
    ctl_zest.bind(mcp, manage, idle)
    ctl_ui.bind(mcp, manage, idle)


def register_bench_tools(mcp: FastMCP, idle: Idle) -> None:
    from backend.mcp_tools.bench import bench_framix
    from backend.mcp_tools.bench import bench_memrix

    bench_framix.bind(mcp, idle)
    bench_memrix.bind(mcp, idle)


def register_common_tools(mcp: FastMCP, idle: Idle) -> None:
    from backend.mcp_tools.common import inspect
    from backend.mcp_tools.common import runtime

    inspect.bind(mcp, idle)
    runtime.bind(mcp, idle)


def register_media_tools(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:
    from backend.mcp_tools.media import audio
    from backend.mcp_tools.media import ffmpeg
    from backend.mcp_tools.media import screen

    audio.bind(mcp, idle)
    ffmpeg.bind(mcp, idle)
    screen.bind(mcp, manage, idle)


def register_all_tools(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:
    register_automator_tools(mcp, manage, idle)
    register_bench_tools(mcp, idle)
    register_common_tools(mcp, idle)
    register_media_tools(mcp, manage, idle)


if __name__ == '__main__':
    pass
