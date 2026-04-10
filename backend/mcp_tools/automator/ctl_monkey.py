# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_hub.hub_monkey import Monkey
from backend.mcp_tools.automator.schemas.schema_app import ActivityArg
from backend.mcp_tools.automator.schemas.schema_monkey import (
    EventsArg,
    GuardActionArg,
    GuardForegroundArg,
    GuardIntervalArg,
    GuardMissThresholdArg,
    GuardStartupGraceArg,
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
            " 默认由 monkey 自己拉起目标应用；运行中默认守护前台，但只记录失焦，不自动接管会话。"
            " 为避免冷启动误判，前台守护默认带启动宽限期。"
            " `saved` 非空时，会在会话结束后把本轮 logcat 导出到该根目录下的独立子目录。"
            " 启动后默认先用 `monkey_status` 看进度，用 `monkey_stop` 主动收束。"
            " 除非用户明确要求等待最终结果，否则不要紧接着调用 `monkey_wait`。"
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
        activity: ActivityArg = None,
        guard_foreground: GuardForegroundArg = True,
        guard_interval_s: GuardIntervalArg = 10.0,
        guard_startup_grace_s: GuardStartupGraceArg = 3.0,
        guard_miss_threshold: GuardMissThresholdArg = 1,
        guard_action: GuardActionArg = "observe",
        saved: MonkeySavedPathArg = None,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "package"               : package,
            "seed"                  : seed,
            "throttle_ms"           : throttle_ms,
            "touch"                 : touch,
            "motion"                : motion,
            "nav"                   : nav,
            "events"                : events,
            "activity"              : activity,
            "guard_foreground"      : guard_foreground,
            "guard_interval_s"      : guard_interval_s,
            "guard_startup_grace_s" : guard_startup_grace_s,
            "guard_miss_threshold"  : guard_miss_threshold,
            "guard_action"          : guard_action,
            "saved"                 : saved
        }

        async def call(device: Device, a: dict) -> typing.Any:
            if sess := await idle.session_get_handle(f"monkey:{device.serial}"):
                return await sess.status(query_reason="already_running")
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
            return Monkey.recent_pack(device.serial, query_reason="no_active_session")

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

            remote_pack = await device.monkey_stop()
            remote_data = dict(remote_pack.get("data") or {})
            recent_pack = Monkey.recent_pack(device.serial, query_reason="no_active_session")
            recent_data = dict(recent_pack.get("data") or {})
            recent_data["remote_stop"] = remote_data
            recent_pack["data"] = recent_data
            recent_pack["text"] = (
                f"{recent_pack.get('text') or '未找到活跃或最近一次 monkey 会话。'}"
                " 已额外向设备发送 monkey 停止命令。"
            )
            return recent_pack

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
                return await sess.status(query_reason="session_active_clear_blocked")

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
