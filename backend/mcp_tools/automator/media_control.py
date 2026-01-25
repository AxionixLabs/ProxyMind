#  __  __          _ _          ____            _             _
# |  \/  | ___  __| (_) __ _   / ___|___  _ __ | |_ _ __ ___ | |
# | |\/| |/ _ \/ _` | |/ _` | | |   / _ \| '_ \| __| '__/ _ \| |
# | |  | |  __/ (_| | | (_| | | |__| (_) | | | | |_| | | (_) | |
# |_|  |_|\___|\__,_|_|\__,_|  \____\___/|_| |_|\__|_|  \___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import (
    DeviceManage, Requires
)
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

    @mcp.tool()
    @task_middleware("start_record")
    async def start_record(local: str, silence: bool) -> CallToolResult:
        """Class: media; Action: 开始录屏/投屏; Args: local(str)=输出路径(文件或目录), silence(bool)=静默录制(隐藏窗口/不显示); Use: 复现流程/长过程取证/视频留档; Returns: CallToolResult(text + structuredContent); Notes: 基于scrcpy启动长任务, 需调用结束录屏/投屏收束并确保文件可播放; 多设备并发时生成独立文件名并写入 local."""
        version = await Requires.connect_scrcpy()

        return await broadcast(
            tool="start_record",
            args={"local": local, "silence": silence},
            target_list=manage.snapshot,
            call=lambda agent: agent.start_record(version, local, silence)
        )

    @mcp.tool()
    @task_middleware("close_record")
    async def close_record() -> CallToolResult:
        """Class: media; Action: 结束录屏/投屏; Args: none; Use: 停止录屏并收束文件(可播放/可归档); Returns: CallToolResult(text + structuredContent); Notes: 建议在录制结束/异常分支都调用一次以避免残留进程。"""
        return await broadcast(
            tool="close_record",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.close_record()
        )


if __name__ == '__main__':
    pass
