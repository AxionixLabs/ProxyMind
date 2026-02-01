#  ____                  _       _____                    _
# | __ )  ___ _ __   ___| |__   |  ___| __ __ _ _ __ ___ (_)_  __
# |  _ \ / _ \ '_ \ / __| '_ \  | |_ | '__/ _` | '_ ` _ \| \ \/ /
# | |_) |  __/ | | | (__| | | | |  _|| | | (_| | | | | | | |>  <
# |____/ \___|_| |_|\___|_| |_| |_|  |_|  \__,_|_| |_| |_|_/_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool()
    @task_middleware("fx_frame_analyzer")
    async def fx_frame_analyzer(title: str, total: str, scale: float = 0.3) -> CallToolResult:
        """Class: framix; Action: 分析视频帧; Args: title(str)=任务标题, total(str)=报告输出目录, scale(float)=视频帧缩放比例(等比缩放，最大1.0，最小0.1); Use: 使用 Framix-画帧秀引擎 对录屏文件列表逐帧分析/抽帧诊断/复现取证并落盘报告; Return: CallToolResult(text + structuredContent); Notes: 依赖 Framix-画帧秀引擎; total 会写入 framix.total 作为报告目录；单任务聚合执行。"""
        await Requires.connect_framix()

        Ins.framix.total = total

        async def call(*_) -> None:
            job_id = await idle.job_begin(
                f"{Ins.framix.agent_id}.fx_frame_analyzer",
                args={"title": title, "total": total, "scale": scale, "video": Ins.video_list}
            )
            try:
                return await Ins.framix.fx_frame_analyzer(title, Ins.video_list, scale)
            finally:
                Ins.video_list.clear()
                await idle.job_final(job_id)

        return await broadcast(
            tool="fx_frame_analyzer",
            args={"title": title, "total": total, "scale": scale, "video": Ins.video_list},
            target_list=[Ins.framix],
            call=call
        )

    @mcp.tool()
    @task_middleware("fx_frame_reporter")
    async def fx_frame_reporter() -> CallToolResult:
        """Class: framix; Action: 生成视频帧阶段分类报告; Args: none; Use: 调用 Framix-画帧秀引擎 汇总视频分析产物并落盘输出最终报告; Return: CallToolResult(text + structuredContent); Notes: 依赖 Framix-画帧秀引擎，且需先执行视频帧分析，完成抽帧/分段数据；单任务聚合执行。"""
        await Requires.connect_framix()

        async def call(*_) -> None:
            job_id = await idle.job_begin(f"{Ins.framix.agent_id}.fx_frame_reporter", args={})
            try:
                return await Ins.framix.fx_frame_reporter()
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="fx_frame_reporter", args={}, target_list=[Ins.framix], call=call
        )


if __name__ == '__main__':
    pass
