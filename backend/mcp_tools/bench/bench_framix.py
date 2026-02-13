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
    @task_middleware("fx_frame_analyzer")
    async def fx_frame_analyzer(title: str, total: str, scale: float = 0.3) -> CallToolResult:
        """
        D: bench
        C: framix
        A: fx_frame_analyzer
        P:
          title: str
          total: str
          scale: float=0.3   # 等比缩放，范围[0.1, 1.0]
        R: CTR
        N:
          - 使用 Framix-画帧秀引擎批量分析视频帧，query_idle 可查询视频状态
          - 输入视频来自内部状态 Ins.video_list（由录制/回填流程写入），不需要视频路径/文件
          - total 作为报告输出目录
        """

        await Requires.connect_framix()

        Ins.framix.total = total

        args = {
            "title": title,
            "total": total,
            "scale": scale
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
    async def fx_frame_reporter() -> CallToolResult:
        """
        D: bench
        C: framix
        A: fx_frame_reporter
        P:
          none
        R: CTR
        N:
          - 生成视频帧最终阶段分类报告（落盘输出）
        """

        await Requires.connect_framix()

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.framix.agent_id}.fx_frame_reporter", args={})
            try:
                return await Ins.framix.fx_frame_reporter()
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="fx_frame_reporter",
            args={},
            target_list=[Ins.framix],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
