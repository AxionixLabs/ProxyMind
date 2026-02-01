#  ____             _            ___        __
# |  _ \  _____   _(_) ___ ___  |_ _|_ __  / _| ___
# | | | |/ _ \ \ / / |/ __/ _ \  | || '_ \| |_ / _ \
# | |_| |  __/\ V /| | (_|  __/  | || | | |  _| (_) |
# |____/ \___| \_/ |_|\___\___| |___|_| |_|_|  \___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("device_snapshot")
    async def device_snapshot() -> CallToolResult:
        """Class: device; Action: 设备状态快照; Args: none; Use: 查看所有设备型号/状态/联网/屏幕/电量；Return: CallToolResult(text + structuredContent); Notes: 每台设备并发采集，失败设备返回异常结果。"""
        return await broadcast(
            tool="device_snapshot",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.device_snapshot()
        )


if __name__ == '__main__':
    pass
