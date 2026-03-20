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
    async def fx_frame_analysis(
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
          total: str?       # 报告输出目录（None 时使用默认规则）
          scale: float=0.3  # 等比缩放，范围[0.1, 1.0]
        R: CTR
        N:
          - 对显式传入的 `video` 列表执行一次 Framix 帧分析。
          - 该工具只使用当前参数中的视频路径，不读取内部视频队列。
          - 结果会按 Framix 规则输出分析产物与报告附件，成败以 `data.ok` 为准。
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
        total: typing.Optional[str] = None,
        scale: float = 0.3
    ) -> CallToolResult:
        """
        D: bench
        C: framix
        A: fx_frame_analyzer
        P:
          title: str
          total: str?=None  # 默认不传
          scale: float=0.3  # 等比缩放，范围[0.1, 1.0]
        R: CTR
        N:
          - 对当前视频队列执行一次 Framix 帧分析。
          - 输入视频来自 `Ins.video_list`，适合接在录制或视频入队链路之后，不需要再手动传视频路径。
          - 执行完成后会清空视频队列；若要复用同一批视频，需要重新入队。
        """

        await Requires.connect_framix()

        args = {
            "video" : Ins.video_list,
            "title" : title,
            "total" : total,
            "scale" : scale
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.framix.agent_id}.fx_frame_analyzer", args=args)
            try:
                return await Ins.framix.fx_frame_analyzer(**args)
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
          - 基于已有 Framix 分析结果生成报告。
          - total 提供时使用指定目录；不提供时使用当前结果目录。
          - 该工具只负责汇总和产出报告，不会重新执行视频分析。
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
