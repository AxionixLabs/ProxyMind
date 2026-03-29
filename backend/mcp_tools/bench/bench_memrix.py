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


FocusArg = typing.Annotated[
    str,
    Field(description="采样目标，一般是包名、进程名或业务焦点标识。"),
]
ImplyArg = typing.Annotated[
    typing.Optional[str],
    Field(description="附加提示信息，用于帮助 Memrix 更准确定位采样目标。"),
]
TaskTitleArg = typing.Annotated[
    typing.Optional[str],
    Field(description="采样任务标题，用于结果目录或报告识别。"),
]
TokenArg = typing.Annotated[
    typing.Optional[str],
    Field(description="要结束的 Memrix 采样任务 token；为空时使用当前会话中的最近任务。"),
]
SceneArg = typing.Annotated[
    typing.Optional[str],
    Field(description="指定已有采样结果目录；为空时使用最近一次结果。"),
]
LayerArg = typing.Annotated[
    bool,
    Field(description="是否输出更细的分层视图。"),
]


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(
        description=(
            "启动一次 Memrix 内存采样任务。"
            "该工具只负责开始采样，不负责结束采样或生成报告。"
            "一次会话只对应一个采样任务；后续需用 `mx_task_final` 收束，再按需调用 reporter 生成报告。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "memrix"}
    )
    @task_middleware("mx_sample_mem")
    async def mx_sample_mem(
        focus: FocusArg,
        imply: ImplyArg = None,
        title: TaskTitleArg = None
    ) -> CallToolResult:

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

    @mcp.tool(
        description=(
            "启动一次 Memrix 图形性能采样任务，用于后续 FPS、jank、流畅度分析。"
            "该工具只负责开始采样，不负责结束采样或生成报告。"
            "一次会话只对应一个采样任务；后续需用 `mx_task_final` 收束，再按需调用 reporter 生成报告。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "memrix"}
    )
    @task_middleware("mx_sample_gfx")
    async def mx_sample_gfx(
        focus: FocusArg,
        imply: ImplyArg = None,
        title: TaskTitleArg = None
    ) -> CallToolResult:

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

    @mcp.tool(
        description=(
            "停止当前或指定 token 对应的 Memrix 采样任务，并收束会话。"
            "该工具只负责结束采样与关闭会话，不负责生成分析报告。"
            "若不传 token，则按当前内部会话状态决定结束哪一个任务。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "memrix"}
    )
    @task_middleware("mx_task_final")
    async def mx_task_final(
        token: TokenArg = None
    ) -> CallToolResult:

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

    @mcp.tool(
        description=(
            "基于已有 Memrix 内存采样结果生成报告。"
            "scene 提供时使用指定结果目录；不提供时使用当前保存的最近一次采样结果。"
            "layer=True 会输出更细的分层视图；未明确需要分层时保持 False 更稳妥。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "memrix"}
    )
    @task_middleware("mx_mem_reporter")
    async def mx_mem_reporter(
        scene: SceneArg = None,
        layer: LayerArg = False
    ) -> CallToolResult:

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

    @mcp.tool(
        description=(
            "基于已有 Memrix 图形采样结果生成流畅度报告。"
            "scene 提供时使用指定结果目录；不提供时使用当前保存的最近一次采样结果。"
            "报告面向 FPS、掉帧、jank 等图形指标分析，不会重新启动采样任务。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "memrix"}
    )
    @task_middleware("mx_gfx_reporter")
    async def mx_gfx_reporter(
        scene: SceneArg = None
    ) -> CallToolResult:

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
