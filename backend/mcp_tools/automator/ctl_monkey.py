# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_hub.hub_monkey import Monkey
from backend.mcp_tools.automator.schemas.schema_monkey import (
    EventsArg,
    MonkeySavedPathArg,
    MotionPctArg,
    NavPctArg,
    SeedArg,
    ThrottleArg,
    TouchPctArg
)
from backend.mcp_tools.shared import (
    MatrixArg,
    PackageArg
)
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "为目标设备启动一次后台 monkey 会话，并立即返回会话信息。"
            " `saved` 非空时，会在会话结束后把本轮 logcat 导出到该根目录下的独立子目录。"
            " 启动后默认应先用 `monkey_status` 轮询进度，用 `monkey_stop` 主动收束。"
            " 除非用户明确要求等待最终结果，否则不要在 `monkey_start` 后立刻调用 `monkey_wait`。"
            " 若同一设备已存在活跃 monkey，会直接返回当前会话状态而不会重复启动。"
        ),
        meta={"hidden": False, "domain": "device", "class": "monkey"}
    )
    @task_middleware("monkey_start")
    async def monkey_start(
        package: PackageArg,
        seed: SeedArg = 42,
        throttle_ms: ThrottleArg = 150,
        touch: TouchPctArg = 65,
        motion: MotionPctArg = 20,
        nav: NavPctArg = 10,
        events: EventsArg = 10000,
        saved: MonkeySavedPathArg = None,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "package"     : package,
            "seed"        : seed,
            "throttle_ms" : throttle_ms,
            "touch"       : touch,
            "motion"      : motion,
            "nav"         : nav,
            "events"      : events,
            "saved"       : saved
        }

        async def call(device: Device, a: dict) -> typing.Any:
            if sess := await idle.session_get_handle(f"monkey:{device.serial}"):
                return await sess.status(reason="already_running")
            monkey = Monkey(device=device, idle=idle)
            return await monkey.start(**a)

        return await broadcast(
            tool="monkey_start",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "查询目标设备当前 monkey 会话状态。"
            " 若没有活跃会话，则返回最近一次 monkey 结果；若从未运行过，则返回 idle 状态。"
        ),
        meta={"hidden": False, "domain": "device", "class": "monkey"}
    )
    @task_middleware("monkey_status")
    async def monkey_status(
        matrix: MatrixArg = None
    ) -> CallToolResult:

        async def call(device: Device, *_) -> typing.Any:
            if sess := await idle.session_get_handle(f"monkey:{device.serial}"):
                return await sess.status()
            return Monkey.recent_pack(device.serial)

        return await broadcast(
            tool="monkey_status",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "等待目标设备当前 monkey 会话结束，并返回最终结果。"
            " 若会话仍在运行，会阻塞直到结束；若当前没有活跃会话，则返回最近一次结果或 idle 状态。"
            " 该工具只适合用户明确要求等待最终结果的场景，默认不要作为 `monkey_start` 后的下一步。"
        ),
        meta={"hidden": False, "domain": "device", "class": "monkey"}
    )
    @task_middleware("monkey_wait")
    async def monkey_wait(
        matrix: MatrixArg = None
    ) -> CallToolResult:

        async def call(device: Device, *_) -> typing.Any:
            if sess := await idle.session_get_handle(f"monkey:{device.serial}"):
                return await sess.wait()
            return Monkey.recent_pack(device.serial, reason="no_active_session")

        return await broadcast(
            tool="monkey_wait",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "停止目标设备当前活跃的 monkey 会话。"
            " 若当前没有活跃会话，则返回最近一次结果或 idle 状态，不会报错中断。"
        ),
        meta={"hidden": False, "domain": "device", "class": "monkey"}
    )
    @task_middleware("monkey_stop")
    async def monkey_stop(
        matrix: MatrixArg = None
    ) -> CallToolResult:

        async def call(device: Device, *_) -> typing.Any:
            if sess := await idle.session_get_handle(f"monkey:{device.serial}"):
                return await sess.stop()
            return Monkey.recent_pack(device.serial, reason="no_active_session")

        return await broadcast(
            tool="monkey_stop",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "清理目标设备最近一次缓存的 monkey 结果。"
            " 该工具不会停止活跃会话；若当前仍有 monkey 在运行，会提示先使用 `monkey_stop`。"
        ),
        meta={"hidden": False, "domain": "device", "class": "monkey"}
    )
    @task_middleware("monkey_clear")
    async def monkey_clear(
        matrix: MatrixArg = None
    ) -> CallToolResult:

        async def call(device: Device, *_) -> typing.Any:
            if sess := await idle.session_get_handle(f"monkey:{device.serial}"):
                return await sess.status(reason="session_active_clear_blocked")

            cleared = Monkey.clear_recent(device.serial)
            return {
                "text"        : "Monkey 最近一次结果已清理。" if cleared else "未找到可清理的 monkey 最近结果。",
                "attachments" : [],
                "data": {
                    "ok"      : True,
                    "serial"  : device.serial,
                    "cleared" : bool(cleared),
                    "reason"  : "cleared" if cleared else "no_recent_result"
                },
                "logs": []
            }

        return await broadcast(
            tool="monkey_clear",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
