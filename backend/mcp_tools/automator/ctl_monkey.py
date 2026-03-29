# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from pydantic import Field
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_hub.hub_monkey import Monkey
from backend.mcp_tools.shared import MatrixArg, PackageArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext, Idle
from backend.utilities.broadcast import broadcast


SeedArg = typing.Annotated[
    int,
    Field(description="monkey 随机种子；相同参数下有助于复现实验。"),
]
ThrottleArg = typing.Annotated[
    int,
    Field(description="两次事件之间的间隔，单位毫秒。"),
]
TouchPctArg = typing.Annotated[
    int,
    Field(description="touch 事件占比。"),
]
MotionPctArg = typing.Annotated[
    int,
    Field(description="motion 事件占比。"),
]
NavPctArg = typing.Annotated[
    int,
    Field(description="导航类事件占比。"),
]
EventsArg = typing.Annotated[
    int,
    Field(description="总事件数。"),
]


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "对指定应用执行一次 `adb shell monkey` 事件注入。"
            " 该工具只注入 touch、motion 和 nav 三类事件，并在执行期间采集异常证据。"
            " 包名不存在、设备不可用或系统策略拦截时会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "monkey"}
    )
    @task_middleware("injection")
    async def injection(
        package: PackageArg,
        seed: SeedArg = 42,
        throttle_ms: ThrottleArg = 150,
        touch: TouchPctArg = 65,
        motion: MotionPctArg = 20,
        nav: NavPctArg = 10,
        events: EventsArg = 10000,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "package"     : package,
            "seed"        : seed,
            "throttle_ms" : throttle_ms,
            "touch"       : touch,
            "motion"      : motion,
            "nav"         : nav,
            "events"      : events
        }

        async def call(device: Device, a: dict) -> typing.Any:
            job_id = await idle.job_begin("monkey.injection", args=a)
            monkey: Monkey = Monkey()
            try:
                return await monkey.injection(device, **a)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="injection",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
