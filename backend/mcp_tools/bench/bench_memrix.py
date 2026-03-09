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
        """
        D: bench
        C: memrix
        A: mx_sample_mem
        P:
          focus: str
          imply: str?=None
          title: str?=None
        R: CTR
        N:
          - 启动 Memrix(记忆星核) 内存采样任务（focus/imply/title 透传给引擎）
          - 单任务执行：一次会话只采集一个目标包/设备
        """

        await Requires.connect_memrix()

        args = {
            "focus" : focus,
            "imply" : imply,
            "title" : title
        }

        async def call(*_) -> typing.Any:
            await idle.session_begin(
                key=Ins.memrix.agent_id,
                name=f"{Ins.memrix.agent_id}.mx_sample_mem",
                args=args
            )
            try:
                resp = await Ins.memrix.mx_task_begin("--storm", focus, imply, title)
                resp = resp or {}

                token = resp.get("data", {}).get("token")
                if token:
                    await idle.session_patch_args(Ins.memrix.agent_id, {"token": token})

                ok = resp.get("data", {}).get("ok")
                if not ok:
                    await idle.session_final(Ins.memrix.agent_id)

                return resp

            except Exception as e:
                await idle.session_final(Ins.memrix.agent_id)
                raise e

        return await broadcast(
            tool="mx_sample_mem",
            args=args,
            target_list=[Ins.memrix],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_sample_gfx")
    async def mx_sample_gfx(
        focus: str,
        imply: typing.Optional[str] = None,
        title: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: memrix
        A: mx_sample_gfx
        P:
          focus: str
          imply: str?=None
          title: str?=None
        R: CTR
        N:
          - 启动 Memrix(记忆星核) 流畅度/帧率采样任务（focus/imply/title 透传给引擎）
          - 单任务执行：一次会话只采集一个目标包/设备
        """

        await Requires.connect_memrix()

        args = {
            "focus" : focus,
            "imply" : imply,
            "title" : title
        }

        async def call(*_) -> typing.Any:
            await idle.session_begin(
                key=Ins.memrix.agent_id,
                name=f"{Ins.memrix.agent_id}.sample_gfx",
                args=args
            )
            try:
                resp = await Ins.memrix.mx_task_begin("--sleek", focus, imply, title)
                resp = resp or {}

                token = resp.get("data", {}).get("token")
                if token:
                    await idle.session_patch_args(Ins.memrix.agent_id, {"token": token})

                ok = resp.get("data", {}).get("ok")
                if not ok:
                    await idle.session_final(Ins.memrix.agent_id)

                return resp

            except Exception as e:
                await idle.session_final(Ins.memrix.agent_id)
                raise e

        return await broadcast(
            tool="sample_gfx",
            args=args,
            target_list=[Ins.memrix],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_task_final")
    async def mx_task_final(
        token: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: memrix
        A: mx_task_final
        P:
          token: str?  # 会话 token（None 时由引擎/内部默认会话决定）
        R: CTR
        N:
          - 停止采集并收束会话：通过 socket(8765) 发送 token 结束采集/关闭流/落盘（若有）
          - 可用 query_idle 查询当前会话 token 状态
          - 单任务聚合执行（非多设备并发）
        """

        await Requires.connect_memrix()

        async def call(*_) -> typing.Any:
            try:
                return await Ins.memrix.mx_task_final(token)
            finally:
                await idle.session_final(Ins.memrix.agent_id)

        return await broadcast(
            tool="mx_task_final",
            args={},
            target_list=[Ins.memrix],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_mem_reporter")
    async def mx_mem_reporter(
        scene: typing.Optional[str] = None,
        layer: bool = False
    ) -> CallToolResult:
        """
        D: bench
        C: memrix
        A: mx_mem_reporter
        P:
          scene: str?=None  # 报告目录分类名（如 202512301120_Storm）
          layer: bool=False
        R: CTR
        N:
          - 生成内存采样报告：用于诊断泄漏/抖动/峰值（Storm）
          - scene 提供时：以 scene 指定的结果目录生成报告
          - scene 不提供时：使用内部回填的最近采样结果（可用 query_idle 查看回填/队列状态）
          - layer=True 时分层展示前台/后台曲线与统计，未明确需要分层时应当为：layer=False
          - 单任务聚合执行（非多设备并发）
        """

        await Requires.connect_memrix()

        args = {
            "scene" : scene,
            "layer" : layer
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.memrix.agent_id}.mx_mem_reporter", args=args)
            try:
                return await Ins.memrix.mx_mem_reporter(scene, layer)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="mx_mem_reporter",
            args=args,
            target_list=[Ins.memrix],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "memrix"})
    @task_middleware("mx_gfx_reporter")
    async def mx_gfx_reporter(
        scene: typing.Optional[str] = None
    ) -> CallToolResult:
        """
        D: bench
        C: memrix
        A: mx_gfx_reporter
        P:
          scene: str?=None  # 报告目录分类名（如 202512301120_Sleek）
        R: CTR
        N:
          - 生成流畅度采样报告：汇总 FPS/掉帧/jank 等指标用于性能诊断与回归对比（Sleek）
          - scene 提供时：以 scene 指定的结果目录生成报告
          - scene 不提供时：使用内部回填的最近采样结果（可用 query_idle 查看回填/队列状态）
          - 单任务聚合执行（非多设备并发）
        """

        await Requires.connect_memrix()

        args = {
            "scene" : scene
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.memrix.agent_id}.mx_gfx_reporter", args=args)
            try:
                return await Ins.memrix.mx_gfx_reporter(scene)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="mx_gfx_reporter",
            args=args,
            target_list=[Ins.memrix],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
