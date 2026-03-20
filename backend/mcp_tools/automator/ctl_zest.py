#   ____ _____ _       _____         _
#  / ___|_   _| |     |__  /___  ___| |_
# | |     | | | |       / // _ \/ __| __|
# | |___  | | | |___   / /|  __/\__ \ |_
#  \____| |_| |_____| /____\___||___/\__|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "tool"})
    @task_middleware("refresh")
    async def refresh(ttl_sec: float = 1.0) -> CallToolResult:
        """
        D: device
        C: tool
        A: refresh
        P:
          ttl_sec: float=1.0
        R: CTR
        N:
          - 刷新可用设备列表：ttl 内复用缓存；超出 ttl 才重扫 adb
          - 输出 devices 数量与 serials 列表（用于执行前更新设备可用性）
        """

        args = {
            "ttl_sec" : ttl_sec
        }

        async def call(*_) -> dict:
            return await manage.refresh_summary(ttl_sec)

        return await broadcast(
            tool="refresh",
            args=args,
            target_list=[None],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
