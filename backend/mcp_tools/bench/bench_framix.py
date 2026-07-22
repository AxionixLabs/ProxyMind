# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.bench.schemas.schema_framix import (
    VideoListArg,
    TotalDirArg,
    LabelArg,
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
            "该工具只使用当前参数中的视频路径，不读取其他工具状态。"
            "`total` 必须显式指定 Framix 结果根目录。"
            "结果会按 Framix 规则输出分析产物与报告附件，成败以 `data.ok` 为准。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "framix"}
    )
    @task_middleware("fx_frame_analysis")
    async def fx_frame_analysis(
        video: VideoListArg,
        total: TotalDirArg,
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
            "对显式传入的 `video` 列表执行一次带标题的 Framix 帧分析。"
            "录屏场景应先结束录制，再传入 `scrcpy_record` 返回的视频路径。"
            "`label` 必须是 YYYYMMDDhhmmss 格式的压缩时间戳，用于唯一标识任务。"
            "执行结果会返回可传给 `fx_frame_reporter` 的 `report_dir`。"
            "该工具不会读取或修改其他工具的内部状态。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "framix"}
    )
    @task_middleware("fx_frame_analyzer")
    async def fx_frame_analyzer(
        video: VideoListArg,
        title: TitleArg,
        label: LabelArg,
        total: TotalDirArg,
        scale: ScaleArg = 0.3
    ) -> CallToolResult:

        await Requires.connect_framix()

        args = {
            "video" : video,
            "title" : title,
            "label" : label,
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
            "`total` 必须使用分析工具明确返回的 `report_dir`，不能省略。"
            "该工具只负责汇总和产出报告，不会重新执行视频分析。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "framix"}
    )
    @task_middleware("fx_frame_reporter")
    async def fx_frame_reporter(
        total: ReportDirArg
    ) -> CallToolResult:

        await Requires.connect_framix()

        args = {
            "total" : total
        }

        job_id = await idle.job_begin(f"{ctx.framix.agent_id}.fx_frame_reporter", args=args)
        try:
            raw = await ctx.framix.fx_frame_reporter(total)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="fx_frame_reporter", args=args, raw=raw, target=ctx.framix.agent_id)


if __name__ == '__main__':
    pass
