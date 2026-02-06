#  ____                  _       __  __                     _
# | __ )  ___ _ __   ___| |__   |  \/  | ___ _ __ ___  _ __(_)_  __
# |  _ \ / _ \ '_ \ / __| '_ \  | |\/| |/ _ \ '_ ` _ \| '__| \ \/ /
# | |_) |  __/ | | | (__| | | | | |  | |  __/ | | | | | |  | |>  <
# |____/ \___|_| |_|\___|_| |_| |_|  |_|\___|_| |_| |_|_|  |_/_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_sample_mem")
    async def mx_sample_mem(
        focus: str,
        imply: typing.Optional[str] = None,
        title: typing.Optional[str] = None
    ) -> CallToolResult:
        """Class: memrix; Action: 采集内存; Args: focus(str)=包名, imply(Optional[str])=设备序列号（可选，默认当前连接的唯一设备）, title (Optional[str])=任务标题（可选）; Use: 启动 Memrix(记忆星核)引擎 内存采样任务(指定包名+设备); Return: CallToolResult(text + structuredContent); Notes: 单任务执行，focus/imply 直接透传给 memrix.task_begin."""
        await Requires.connect_memrix()

        async def call(*_) -> None:
            await idle.session_begin(
                key=Ins.memrix.agent_id,
                name=f"{Ins.memrix.agent_id}.mx_sample_mem",
                args={"style": "storm", "focus": focus, "imply": imply, "title": title},
            )
            try:
                return await Ins.memrix.mx_task_begin("--storm", focus, imply, title)
            except Exception as e:
                await idle.session_final(Ins.memrix.agent_id)
                raise e

        return await broadcast(
            tool="mx_sample_mem",
            args={"style": "storm", "focus": focus, "imply": imply, "title": title},
            target_list=[Ins.memrix],
            call=call
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_sample_gfx")
    async def mx_sample_gfx(
        focus: str,
        imply: typing.Optional[str] = None,
        title: typing.Optional[str] = None
    ) -> CallToolResult:
        """Class: memrix; Action: 采集流畅度/帧率; Args: focus(str)=包名, imply(Optional[str])=设备序列号（可选，默认当前连接的唯一设备）, title (Optional[str])=任务标题（可选）; Use: 启动 Memrix(记忆星核)引擎 流畅度采样任务(指定包名+设备); Return: CallToolResult(text + structuredContent); Notes: 单任务执行，focus/imply 直接透传给 memrix.task_begin."""
        await Requires.connect_memrix()

        async def call(*_) -> None:
            await idle.session_begin(
                key=Ins.memrix.agent_id,
                name=f"{Ins.memrix.agent_id}.sample_gfx",
                args={"style": "sleek", "focus": focus, "imply": imply, "title": title}
            )
            try:
                return await Ins.memrix.mx_task_begin("--sleek", focus, imply, title)
            except Exception as e:
                await idle.session_final(Ins.memrix.agent_id)
                raise e

        return await broadcast(
            tool="sample_gfx",
            args={"style": "sleek", "focus": focus, "imply": imply, "title": title},
            target_list=[Ins.memrix],
            call=call
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_task_final")
    async def mx_task_final() -> CallToolResult:
        """Class: memrix; Action: 停止采集并收束任务; Args: none; Use: 通过 socket 调用 8765 端口发送 token 结束采集会话/关闭流并落盘(若有); Return: CallToolResult(text + structuredContent); Notes: 单任务聚合执行(非多设备并发)。"""
        await Requires.connect_memrix()

        async def call(*_) -> None:
            try:
                return await Ins.memrix.mx_task_final()
            finally:
                await idle.session_final(Ins.memrix.agent_id)

        return await broadcast(
            tool="mx_task_final", args={}, target_list=[Ins.memrix], call=call
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_mem_reporter")
    async def mx_mem_reporter(layer: bool = False) -> CallToolResult:
        """Class: memrix; Action: 生成内存采样报告; Args: layer(bool)=是否分层展示(前台/后台)的内存曲线与统计; Use: 调用 Memrix-记忆星核引擎 生成内存报告用于诊断泄漏/抖动/峰值; Return: CallToolResult(text + structuredContent); Notes: 依赖 Memrix-记忆星核引擎; 单任务聚合执行(非多设备并发)。"""
        await Requires.connect_memrix()

        async def call(*_) -> None:
            job_id = await idle.job_begin(f"{Ins.memrix.agent_id}.mx_mem_reporter", args={"layer": layer})
            try:
                return await Ins.memrix.mx_mem_reporter(layer)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="mx_mem_reporter", args={"layer": layer}, target_list=[Ins.memrix], call=call
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_gfx_reporter")
    async def mx_gfx_reporter() -> CallToolResult:
        """Class: memrix; Action: 生成流畅度采样报告; Args: none; Use: 调用 Memrix-记忆星核引擎 汇总并落盘帧率/掉帧/jank 等指标用于性能诊断与回归对比; Return: CallToolResult(text + structuredContent); Notes: 依赖 Memrix-记忆星核引擎; 单任务聚合执行(非多设备并发)。"""
        await Requires.connect_memrix()

        async def call(*_) -> None:
            job_id = await idle.job_begin(f"{Ins.memrix.agent_id}.mx_gfx_reporter", args={})
            try:
                return await Ins.memrix.mx_gfx_reporter()
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="mx_gfx_reporter", args={}, target_list=[Ins.memrix], call=call
        )


if __name__ == '__main__':
    pass

