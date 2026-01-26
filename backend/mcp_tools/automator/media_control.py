#  __  __          _ _          ____            _             _
# |  \/  | ___  __| (_) __ _   / ___|___  _ __ | |_ _ __ ___ | |
# | |\/| |/ _ \/ _` | |/ _` | | |   / _ \| '_ \| __| '__/ _ \| |
# | |  | |  __/ (_| | | (_| | | |__| (_) | | | | |_| | | (_) | |
# |_|  |_|\___|\__,_|_|\__,_|  \____\___/|_| |_|\__|_|  \___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("screenshot")
    async def screenshot() -> CallToolResult:
        """Class: media; Action: 截取当前屏幕截图; Args: none; Use: 取证/调试/执行后验证; Return: CallToolResult(text + structuredContent); Notes: 截图结果（可能包含路径/bytes/metadata，依 device 实现而定）。"""
        return await broadcast(
            tool="screenshot",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.screenshot()
        )


if __name__ == '__main__':
    pass
