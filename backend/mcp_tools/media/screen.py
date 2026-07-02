# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import (
    DeviceManage, Requires
)
from backend.mcp_hub.hub_record import Record
from backend.mcp_tools.media.schemas.schema_screen import (
    RecordDirectoryArg,
    RecordFpsArg,
    SilenceArg
)
from backend.mcp_tools.shared import SerialArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.tool_result import build_tool_result
from backend.utilities.runtime import (
    AppContext, Idle
)


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "为目标设备启动一次 scrcpy 镜像会话。"
            "该工具只负责打开镜像会话，不负责收束；后续应调用 `scrcpy_close` 结束会话。"
            "多设备连接时应通过 `serial` 指定目标设备。"
        ),
        meta={"hidden": False, "domain": "media", "class": "scrcpy"}
    )
    @task_middleware("scrcpy_mirror")
    async def scrcpy_mirror(
        serial: SerialArg = None
    ) -> CallToolResult:

        version = await Requires.connect_scrcpy()

        device = manage.resolve(serial)

        record = Record(
            device=device,
            idle=idle,
            version=version,
        )

        raw = await record.scrcpy_mirror()

        return build_tool_result(tool="scrcpy_mirror", args={}, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "为目标设备启动一次 scrcpy 录屏会话。"
            "该工具只负责开始录制并返回会话信息；后续应调用 `scrcpy_close` 收束录制并释放资源。"
            "多设备连接时应通过 `serial` 指定目标设备。"
        ),
        meta={"hidden": False, "domain": "media", "class": "scrcpy"}
    )
    @task_middleware("scrcpy_record")
    async def scrcpy_record(
        directory: RecordDirectoryArg = None,
        fps: RecordFpsArg = 60,
        silence: SilenceArg = False,
        serial: SerialArg = None
    ) -> CallToolResult:

        version = await Requires.connect_scrcpy()

        args = {
            "directory" : directory,
            "fps"       : fps,
            "silence"   : silence
        }

        device = manage.resolve(serial)

        record = Record(
            device=device,
            idle=idle,
            version=version,
        )

        raw = await record.scrcpy_record(**args)
        if video_temp := raw.data.get("path"):
            await ctx.video_list_append(video_temp)

        return build_tool_result(tool="scrcpy_record", args=args, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "关闭目标设备当前活跃的 scrcpy 会话。"
            "该工具用于收束 `scrcpy_mirror` 或 `scrcpy_record` 打开的长会话，并释放相关资源。"
            "若当前没有活跃会话，则按“无需关闭”处理，不会报错中断。"
        ),
        meta={"hidden": False, "domain": "media", "class": "scrcpy"}
    )
    @task_middleware("scrcpy_close")
    async def scrcpy_close(
        serial: SerialArg = None
    ) -> CallToolResult:

        device = manage.resolve(serial)
        if not (sess := await idle.session_get_handle(f"scrcpy:{device.serial}")):
            raw = Record.no_active_session(device.serial)
        else:
            raw = await sess.scrcpy_close()

        return build_tool_result(tool="scrcpy_close", args={}, raw=raw, target=device.serial)


if __name__ == '__main__':
    pass
