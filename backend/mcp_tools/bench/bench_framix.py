#  ____                  _       _____                    _
# | __ )  ___ _ __   ___| |__   |  ___| __ __ _ _ __ ___ (_)_  __
# |  _ \ / _ \ '_ \ / __| '_ \  | |_ | '__/ _` | '_ ` _ \| \ \/ /
# | |_) |  __/ | | | (__| | | | |  _|| | | (_| | | | | | | |>  <
# |____/ \___|_| |_|\___|_| |_| |_|  |_|  \__,_|_| |_| |_|_/_/\_\
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

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "framix"})
    @task_middleware("fx_frame_analysis")
    async def fx_analysis(
        video: list[str],
        total: typing.Optional[str],
        scale: float = 0.3
    ) -> CallToolResult:
        """
        D: bench
        C: framix
        A: fx_frame_analysis
        P:
          video: list[str]  # 显式视频路径列表（会展开为 --video v1 --video v2 ...）
          total: str?       # 报告输出目录（None 时由引擎/内部规则决定）
          scale: float=0.3  # 等比缩放，范围[0.1, 1.0]
        R: CTR
        N:
          - Framix 批量分析入口（显式输入）：对 video 列表逐个执行帧分析/抽帧诊断并汇总落盘
          - 非内部回填：不读取 Ins.video_list
          - 多模态对齐输出：text/attachments/data/logs（成败以 data.ok 为准）
        """

        await Requires.connect_framix()

        args = {
            "video" : video,
            "total" : total,
            "scale" : scale
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.framix.agent_id}.fx_frame_analysis", args=args)
            try:
                return await Ins.framix.fx_frame_analysis(video, total, scale)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="fx_frame_analysis",
            args=args,
            target_list=[Ins.framix],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "framix"})
    @task_middleware("fx_frame_analyzer")
    async def fx_frame_analyzer(
        title: str,
        total: str,
        scale: float = 0.3
    ) -> CallToolResult:
        """
        D: bench
        C: framix
        A: fx_frame_analyzer
        P:
          title: str
          total: str
          scale: float=0.3  # 等比缩放，范围[0.1, 1.0]
        R: CTR
        N:
          - Framix 批量分析入口（内部回填）：输入视频来自 Ins.video_list（由录制/回填流程写入），无需传视频路径
          - total 作为报告输出目录；执行后会清空 Ins.video_list
          - query_idle 可用于查看当前回填/队列状态
          - 多模态对齐输出：text/attachments/data/logs（成败以 data.ok 为准）
        """

        await Requires.connect_framix()

        Ins.framix.total = total

        args = {
            "title" : title,
            "total" : total,
            "scale" : scale
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.framix.agent_id}.fx_frame_analyzer", args=args)
            try:
                return await Ins.framix.fx_frame_analyzer(title, Ins.video_list, scale)
            finally:
                Ins.video_list.clear()
                await idle.job_final(job_id)

        return await broadcast(
            tool="fx_frame_analyzer",
            args=args,
            target_list=[Ins.framix],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "framix"})
    @task_middleware("fx_frame_reporter")
    async def fx_frame_reporter(total: typing.Optional[str] = None) -> CallToolResult:
        """
        D: bench
        C: framix
        A: fx_frame_reporter
        P:
          total: str?=None
        R: CTR
        N:
          - 生成视频帧分析报告，阶段分类报告
          - total 提供时：以 total 作为目标目录/报告根目录
          - total 不提供时：使用内部回填的报告路径作为输入（可用 query_idle 查看当前回填/队列状态）
          - 多模态对齐输出：text/attachments/data/logs（成败以 data.ok 为准）
        """

        await Requires.connect_framix()

        args = {
            "total" : total
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.framix.agent_id}.fx_frame_reporter", args=args)
            try:
                return await Ins.framix.fx_frame_reporter()
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="fx_frame_reporter",
            args=args,
            target_list=[Ins.framix],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
