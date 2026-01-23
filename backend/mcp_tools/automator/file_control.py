#  _____ _ _         ____            _             _
# |  ___(_) | ___   / ___|___  _ __ | |_ _ __ ___ | |
# | |_  | | |/ _ \ | |   / _ \| '_ \| __| '__/ _ \| |
# |  _| | | |  __/ | |__| (_) | | | | |_| | | (_) | |
# |_|   |_|_|\___|  \____\___/|_| |_|\__|_|  \___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("pull")
    async def pull(remote: str, local: str) -> CallToolResult:
        """Class: file; Action: 拉取文件(adb pull); Args: remote(str), local(str); Use: 拉取设备文件到本机; Return: CallToolResult(text + structuredContent); Notes: local 建议带目录, 多设备时会写同一路径需注意冲突。"""
        return await broadcast(
            tool="pull",
            args={"remote": remote, "local": local},
            target_list=manage.snapshot,
            call=lambda agent: agent.pull(remote, local)
        )

    @mcp.tool()
    @task_middleware("push")
    async def push(local: str, remote: str) -> CallToolResult:
        """Class: file; Action: 推送文件(adb push); Args: local(str), remote(str); Use: 推送本机文件到设备; Return: CallToolResult(text + structuredContent); Notes: remote 需可写权限(如 /sdcard/...)."""
        return await broadcast(
            tool="push",
            args={"local": local, "remote": remote},
            target_list=manage.snapshot,
            call=lambda agent: agent.push(local, remote)
        )

    @mcp.tool()
    @task_middleware("remove")
    async def remove(path: str) -> CallToolResult:
        """Class: file; Action: 删除设备端文件(rm -f); Args: path(str); Use: 清理截图/日志/临时文件; Return: CallToolResult(text + structuredContent); Notes: 仅删除文件(不删目录), 不存在则忽略。"""
        return await broadcast(
            tool="remove",
            args={"path": path},
            target_list=manage.snapshot,
            call=lambda agent: agent.remove(path)
        )


if __name__ == '__main__':
    pass
