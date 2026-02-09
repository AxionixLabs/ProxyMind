#  ____
# / ___|  ___ _ __ ___  ___ _ __
# \___ \ / __| '__/ _ \/ _ \ '_ \
#  ___) | (__| | |  __/  __/ | | |
# |____/ \___|_|  \___|\___|_| |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import (
    DeviceManage, Requires
)
from backend.mcp_hub.hub_record import Record
from backend.middlewares.mid_task import task_middleware
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "scrcpy"})
    @task_middleware("scrcpy_mirror")
    async def scrcpy_mirror(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: media
        C: scrcpy
        A: scrcpy_mirror
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 启动 scrcpy 镜像长任务，用于远程观察/问题复现/交互调试
          - 每台设备创建独立 Record 会话并写入 sessions[serial]，供后续 scrcpy_close 收束
        """

        version = await Requires.connect_scrcpy()

        async def call(device: Device, *_) -> typing.Any:
            _, on_begin, on_final = idle.hooks(
                "scrcpy.scrcpy_mirror",
                args={},
                args_fn=lambda: {"serial": device.serial, "brand": device.brand}
            )
            record: Record = Record(
                device, version, Ins.station, Ins.sessions, Ins.sessions_lock,
                on_begin=on_begin, on_final=on_final
            )
            return await record.scrcpy_mirror()

        return await broadcast(
            tool="scrcpy_mirror",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "scrcpy"})
    @task_middleware("scrcpy_record")
    async def scrcpy_record(
        directory: str,
        fps: int = 60,
        silence: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: media
        C: scrcpy
        A: scrcpy_record
        P:
          directory: str
          fps: int=60
          silence: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 启动 scrcpy 录屏长任务：每台设备生成独立视频文件并返回路径
          - 同时保存 Record 会话到 sessions[serial]，供后续 scrcpy_close 关闭/清理
        """

        version = await Requires.connect_scrcpy()

        args = {
            "directory" : directory,
            "fps"       : fps,
            "silence"   : silence
        }

        async def call(device: Device, a: dict) -> typing.Any:
            _, on_begin, on_final = idle.hooks(
                "scrcpy.scrcpy_record",
                args=a,
                args_fn=lambda: {"serial": device.serial, "brand": device.brand}
            )
            record: Record = Record(
                device, version, Ins.station, Ins.sessions, Ins.sessions_lock,
                on_begin=on_begin, on_final=on_final
            )
            mm_resp = await record.scrcpy_record(**a)
            if video_temp := mm_resp.get("data", {}).get("path"):
                async with Ins.video_lock:
                    Ins.video_list.append(video_temp)
            return mm_resp

        return await broadcast(
            tool="scrcpy_record",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "media", "class": "scrcpy"})
    @task_middleware("scrcpy_close")
    async def scrcpy_close(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: media
        C: scrcpy
        A: scrcpy_close
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 停止 scrcpy_mirror/scrcpy_record 并释放资源（确保窗口关闭/文件可用）
          - 从 sessions[serial] 取 Record 会话执行 close；无会话则跳过；结束后移除会话避免泄漏
        """

        async def call(device: Device, *_) -> typing.Any:
            async with Ins.sessions_lock:
                if not (sess := Ins.sessions.get(device.serial)):
                    return {
                        "text"        : "未找到活跃的 scrcpy 会话，无需关闭。",
                        "attachments" : [],
                        "data": {
                            "ok"     : True,
                            "serial" : device.serial,
                            "reason" : "no_active_session",
                        },
                        "logs": []
                    }
            return await sess.scrcpy_close()

        return await broadcast(
            tool="scrcpy_close",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
