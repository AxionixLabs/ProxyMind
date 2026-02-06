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
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "media"})
    @task_middleware("screenshot")
    async def screenshot(local: str) -> CallToolResult:
        """Class: media; Action: 截取当前屏幕截图并保存到本地路径; Args: local(str); Use: 取证/调试/执行后验证; Return: CallToolResult(text + structuredContent); Notes: 本地保存路径/目录，若为目录将自动生成文件名。"""
        return await broadcast(
            tool="screenshot",
            args={"local": local},
            target_list=manage.snapshot,
            call=lambda agent: agent.screenshot(local)
        )


if __name__ == '__main__':
    pass
