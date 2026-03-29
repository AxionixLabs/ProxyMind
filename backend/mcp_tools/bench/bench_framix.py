# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from pydantic import Field
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


VideoListArg = typing.Annotated[
    list[str],
    Field(description="待分析的视频文件路径列表。"),
]
ReportDirArg = typing.Annotated[
    typing.Optional[str],
    Field(description="Framix 结果目录或报告目录；为空时使用当前默认结果目录。"),
]
ScaleArg = typing.Annotated[
    float,
    Field(description="分析前的缩放比例，用于平衡速度与细节。"),
]
TitleArg = typing.Annotated[
    str,
    Field(description="本次分析任务标题，用于结果目录或报告标识。"),
]


def bind(mcp: FastMCP, idle: Idle) -> None:

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

    @mcp.tool(
        description=(
            "对当前视频队列执行一次 Framix 帧分析。"
            "输入视频来自 `Ins.video_list`，适合接在录制或视频入队链路之后，不需要再手动传视频路径。"
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

    @mcp.tool(
        description=(
            "基于已有 Framix 分析结果生成报告。"
            "total 提供时使用指定目录；不提供时使用当前结果目录。"
            "该工具只负责汇总和产出报告，不会重新执行视频分析。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "framix"}
    )
    @task_middleware("fx_frame_reporter")
    async def fx_frame_reporter(total: ReportDirArg = None) -> CallToolResult:

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
