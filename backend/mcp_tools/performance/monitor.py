#  __  __             _ _
# |  \/  | ___  _ __ (_) |_ ___  _ __
# | |\/| |/ _ \| '_ \| | __/ _ \| '__|
# | |  | | (_) | | | | | || (_) | |
# |_|  |_|\___/|_| |_|_|\__\___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
import typing
import asyncio
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_core.core_fx import Framix
from backend.mcp_core.core_mx import Memrix
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import (
    DeviceManage, Requires
)
from backend.mcp_hub.hub_record import Record
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import (
    kill_port, broadcast, Idle
)


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:

    station: str = sys.platform

    sessions: dict[str, Record] = {}
    sessions_lock: asyncio.Lock = asyncio.Lock()

    video_list: list[str] = []
    video_lock: asyncio.Lock = asyncio.Lock()

    framix: Framix = Framix()
    memrix: Memrix = Memrix()

    @mcp.tool()
    @task_middleware("start_mirror")
    async def start_mirror() -> CallToolResult:
        """Class: monitor; Action: 开始投屏(镜像); Args: none; Use: 远程观察/问题复现/配合交互调试; Return: CallToolResult(text + structuredContent); Notes: 基于 scrcpy 启动镜像长任务；每台设备创建独立 Record 会话并写入 sessions[device.serial]，用于后续 close_record 统一收束。"""
        version = await Requires.connect_scrcpy()

        async def call(device: Device) -> None:
            record: Record = Record(
                device, version, station, sessions, sessions_lock, on_begin=idle.job_begin, on_final=idle.job_final
            )
            await record.ask_start_mirror()

        return await broadcast(
            tool="start_mirror", args={}, target_list=manage.snapshot, call=call
        )

    @mcp.tool()
    @task_middleware("start_record")
    async def start_record(directory: str, fps: int = 60, silence: bool = False) -> CallToolResult:
        """Class: monitor; Action: 开始录屏; Args: directory(str)=输出路径(目录), fps(int)=视频帧率, silence(bool)=静默录制(隐藏窗口/不显示); Use: 复现流程/长过程取证/视频留档; Return: CallToolResult(text + structuredContent); Notes: 基于 scrcpy 启动录制长任务；每台设备生成独立文件名并返回视频路径，同时保存 Record 会话到 sessions[device.serial] 以便 close_record 关闭与清理。"""
        version = await Requires.connect_scrcpy()

        async def call(device: Device) -> typing.Optional[str]:
            record: Record = Record(
                device, version, station, sessions, sessions_lock, on_begin=idle.job_begin, on_final=idle.job_final
            )
            video_temp = await record.ask_start_record(directory, fps, silence)
            async with video_lock:
                video_list.append(video_temp)
            return video_temp

        return await broadcast(
            tool="start_record",
            args={"directory": directory, "fps": fps, "silence": silence},
            target_list=manage.snapshot,
            call=call
        )

    @mcp.tool()
    @task_middleware("close_record")
    async def close_record() -> CallToolResult:
        """Class: monitor; Action: 停止录屏/投屏并释放资源; Args: none; Use: 结束 start_record/start_mirror 的长任务并确保文件可播放/窗口关闭; Return: CallToolResult(text + structuredContent); Notes: 按 device.serial 从 sessions 取 Record 会话并调用 ask_close_record；无会话则跳过；无论成功/失败都会从 sessions 移除避免泄漏。"""

        async def call(device: Device) -> None:
            async with sessions_lock:
                if not (sess := sessions.get(device.serial)):
                    return None
            return await sess.ask_close_record()

        return await broadcast(
            tool="close_record", args={}, target_list=manage.snapshot, call=call
        )

    @mcp.tool()
    @task_middleware("frame_analyzer")
    async def frame_analyzer(title: str, total: str, scale: float = 0.3) -> CallToolResult:
        """Class: monitor; Action: 分析视频帧; Args: title(str)=任务标题, total(str)=报告输出目录, scale(float)=视频帧缩放比例(等比缩放，最大1.0，最小0.1); Use: 使用 Framix(画帧秀)引擎 对录屏文件列表逐帧分析/抽帧诊断/复现取证并落盘报告; Return: CallToolResult(text + structuredContent); Notes: 依赖 Framix 引擎; total 会写入 framix.total 作为报告目录; 当前为单任务聚合执行(非多设备并发)。"""
        await Requires.connect_framix()

        framix.total = total

        async def call(*_) -> None:
            await idle.job_begin()
            try:
                return await framix.frame_analyzer(title, video_list, scale)
            finally:
                video_list.clear()
                await idle.job_final()

        return await broadcast(
            tool="frame_analyzer", args={"title": title, "total": total}, target_list=[None], call=call
        )

    @mcp.tool()
    @task_middleware("frame_reporter")
    async def frame_reporter() -> CallToolResult:
        """Class: monitor; Action: 生成视频帧阶段分类报告; Args: none; Use: 调用 Framix(画帧秀)引擎 汇总analyzer产物并落盘输出最终报告; Return: CallToolResult(text + structuredContent); Notes: 依赖 Framix 引擎且需先执行analyzer完成抽帧/分段数据；单任务聚合执行。"""
        await Requires.connect_framix()

        async def call(*_) -> None:
            await idle.job_begin()
            try:
                return await framix.frame_reporter()
            finally:
                await idle.job_final()

        return await broadcast(
            tool="frame_reporter", args={}, target_list=[None], call=call
        )

    @mcp.tool()
    @task_middleware("sample_mem")
    async def sample_mem(focus: str, imply: str) -> CallToolResult:
        """Class: monitor; Action: 采集内存; Args: focus(str)=包名, imply(str)=设备序列号; Use: 启动 Memrix(记忆星核)引擎 内存采样任务(指定包名+设备); Return: CallToolResult(text + structuredContent); Notes: 单任务执行，focus/imply 直接透传给 memrix.task_begin."""
        await Requires.connect_memrix()
        await kill_port(memrix.port)

        async def call(*_) -> None:
            await idle.job_begin()
            return await memrix.task_begin("--storm", focus, imply)

        return await broadcast(
            tool="sample_mem", args={"focus": focus, "imply": imply}, target_list=[None], call=call
        )

    @mcp.tool()
    @task_middleware("sample_gfx")
    async def sample_gfx(focus: str, imply: str) -> CallToolResult:
        """Class: monitor; Action: 采集流畅度; Args: focus(str)=包名, imply(str)=设备序列号; Use: 启动 Memrix(记忆星核)引擎 流畅度采样任务(指定包名+设备); Return: CallToolResult(text + structuredContent); Notes: 单任务执行，focus/imply 直接透传给 memrix.task_begin."""
        await Requires.connect_memrix()
        await kill_port(memrix.port)

        async def call(*_) -> None:
            await idle.job_begin()
            return await memrix.task_begin("--sleek", focus, imply)

        return await broadcast(
            tool="sample_gfx", args={"focus": focus, "imply": imply}, target_list=[None], call=call
        )

    @mcp.tool()
    @task_middleware("sample_stop")
    async def sample_stop() -> CallToolResult:
        """Class: monitor; Action: 停止采集并收束任务; Args: none; Use: 通过 socket 调用 task_final() 结束采集会话/关闭流并落盘(若有); Return: CallToolResult(text + structuredContent); Notes: 单任务聚合执行(非多设备并发)。"""
        await Requires.connect_memrix()

        async def call(*_) -> None:
            try:
                return await memrix.task_final()
            finally:
                await idle.job_final()

        return await broadcast(
            tool="sample_stop", args={}, target_list=[None], call=call
        )

    @mcp.tool()
    @task_middleware("mem_reporter")
    async def mem_reporter(layer: bool = False) -> CallToolResult:
        """Class: monitor; Action: 生成内存采样报告; Args: layer(bool)=是否分层展示(前台/后台)的内存曲线与统计; Use: 调用 Memrix(记忆星核)引擎 生成内存报告用于诊断泄漏/抖动/峰值; Return: CallToolResult(text + structuredContent); Notes: 依赖 Memrix(记忆星核)引擎; 单任务聚合执行(非多设备并发)。"""
        await Requires.connect_memrix()

        async def call(*_) -> None:
            await idle.job_begin()
            try:
                return await memrix.mem_reporter(layer)
            finally:
                await idle.job_final()

        return await broadcast(
            tool="mem_reporter", args={"layer": layer}, target_list=[None], call=call
        )

    @mcp.tool()
    @task_middleware("gfx_reporter")
    async def gfx_reporter() -> CallToolResult:
        """Class: monitor; Action: 生成流畅度采样报告; Args: none; Use: 调用 Memrix(记忆星核)引擎 汇总并落盘帧率/掉帧/jank 等指标用于性能诊断与回归对比; Return: CallToolResult(text + structuredContent); Notes: 依赖 Memrix(记忆星核)引擎; 单任务聚合执行(非多设备并发)。"""
        await Requires.connect_memrix()

        async def call(*_) -> None:
            await idle.job_begin()
            try:
                return await memrix.gfx_reporter()
            finally:
                await idle.job_final()

        return await broadcast(
            tool="gfx_reporter", args={}, target_list=[None], call=call
        )


if __name__ == '__main__':
    pass
