# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.bench.schemas.schema_framix import (
    VideoListArg,
    ReportDirArg,
    ScaleArg,
    TitleArg
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.tool_result import build_tool_result


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "对显式传入的 `video` 列表执行一次 Framix 帧分析。"
            "该工具只使用当前参数中的视频路径，不读取内部视频队列。"
            "结果会按 Framix 规则输出分析产物与报告附件，成败以 `data.ok` 为准。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "framix"}
    )
    @task_middleware("fx_frame_analysis")
    async def fx_frame_analysis(
        video: VideoListArg,
        total: ReportDirArg,
        scale: ScaleArg = 0.3
    ) -> CallToolResult:

        await Requires.connect_framix()

        args = {
            "video" : video,
            "total" : total,
            "scale" : scale
        }

        job_id = await idle.job_begin(f"{ctx.framix.agent_id}.fx_frame_analysis", args=args)
        try:
            raw = await ctx.framix.fx_frame_analysis(video, total, scale)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="fx_frame_analysis", args=args, raw=raw, target=ctx.framix.agent_id)

    @mcp.tool(
        description=(
            "对当前视频队列执行一次 Framix 帧分析。"
            "输入视频来自当前内部视频队列，适合接在录制或视频入队链路之后，不需要再手动传视频路径。"
            "执行完成后会清空视频队列；若要复用同一批视频，需要重新入队。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "framix"}
    )
    @task_middleware("fx_frame_analyzer")
    async def fx_frame_analyzer(
        title: TitleArg,
        total: ReportDirArg = None,
        scale: ScaleArg = 0.3
    ) -> CallToolResult:

        await Requires.connect_framix()

        videos = await ctx.video_list_take_all()

        args = {
            "video" : videos,
            "title" : title,
            "total" : total,
            "scale" : scale
        }

        job_id = await idle.job_begin(f"{ctx.framix.agent_id}.fx_frame_analyzer", args=args)
        try:
            raw = await ctx.framix.fx_frame_analyzer(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="fx_frame_analyzer", args=args, raw=raw, target=ctx.framix.agent_id)

    @mcp.tool(
        description=(
            "基于已有 Framix 分析结果生成报告。"
            "total 提供时使用指定目录；不提供时使用当前结果目录。"
            "该工具只负责汇总和产出报告，不会重新执行视频分析。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "framix"}
    )
    @task_middleware("fx_frame_reporter")
    async def fx_frame_reporter(
        total: ReportDirArg = None
    ) -> CallToolResult:

        await Requires.connect_framix()

        args = {
            "total" : total
        }

        job_id = await idle.job_begin(f"{ctx.framix.agent_id}.fx_frame_reporter", args=args)
        try:
            raw = await ctx.framix.fx_frame_reporter()
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="fx_frame_reporter", args=args, raw=raw, target=ctx.framix.agent_id)


if __name__ == '__main__':
    pass
