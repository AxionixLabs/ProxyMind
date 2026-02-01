#  ____                             ____                        _
# / ___|  ___ _ __ ___  ___ _ __   |  _ \ ___  ___ ___  _ __ __| |
# \___ \ / __| '__/ _ \/ _ \ '_ \  | |_) / _ \/ __/ _ \| '__/ _` |
#  ___) | (__| | |  __/  __/ | | | |  _ <  __/ (_| (_) | | | (_| |
# |____/ \___|_|  \___|\___|_| |_| |_| \_\___|\___\___/|_|  \__,_|
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

    @mcp.tool()
    @task_middleware("scrcpy_mirror")
    async def scrcpy_mirror() -> CallToolResult:
        """Class: scrcpy; Action: 开始投屏(镜像); Args: none; Use: 远程观察/问题复现/配合交互调试; Return: CallToolResult(text + structuredContent); Notes: 基于 scrcpy 启动镜像长任务；每台设备创建独立 Record 会话并写入 sessions[device.serial]，用于后续 scrcpy_close 统一收束。"""
        version = await Requires.connect_scrcpy()

        async def call(device: Device) -> None:
            _, on_begin, on_final = idle.hooks(
                "scrcpy.scrcpy_mirror",
                args={"tool": "scrcpy_mirror"},
                args_fn=lambda: {"serial": device.serial, "brand": device.brand}
            )
            record: Record = Record(
                device, version, Ins.station, Ins.sessions, Ins.sessions_lock,
                on_begin=on_begin, on_final=on_final
            )
            await record.scrcpy_mirror()

        return await broadcast(
            tool="scrcpy_mirror", args={}, target_list=manage.snapshot, call=call
        )

    @mcp.tool()
    @task_middleware("scrcpy_record")
    async def start_record(directory: str, fps: int = 60, silence: bool = False) -> CallToolResult:
        """Class: scrcpy; Action: 开始录屏; Args: directory(str)=输出路径(目录), fps(int)=视频帧率, silence(bool)=静默录制(隐藏窗口/不显示); Use: 复现流程/长过程取证/视频留档; Return: CallToolResult(text + structuredContent); Notes: 基于 scrcpy 启动录制长任务；每台设备生成独立文件名并返回视频路径，同时保存 Record 会话到 sessions[device.serial] 以便 scrcpy_close 关闭与清理。"""
        version = await Requires.connect_scrcpy()

        async def call(device: Device) -> typing.Optional[str]:
            _, on_begin, on_final = idle.hooks(
                "scrcpy.scrcpy_record",
                args={"tool": "scrcpy_record"},
                args_fn=lambda: {"serial": device.serial, "brand": device.brand}
            )
            record: Record = Record(
                device, version, Ins.station, Ins.sessions, Ins.sessions_lock,
                on_begin=on_begin, on_final=on_final
            )
            video_temp = await record.scrcpy_record(directory, fps, silence)
            async with Ins.video_lock:
                Ins.video_list.append(video_temp)
            return video_temp

        return await broadcast(
            tool="scrcpy_record",
            args={"directory": directory, "fps": fps, "silence": silence},
            target_list=manage.snapshot,
            call=call
        )

    @mcp.tool()
    @task_middleware("scrcpy_close")
    async def scrcpy_close() -> CallToolResult:
        """Class: scrcpy; Action: 停止录屏/投屏并释放资源; Args: none; Use: 结束 scrcpy_mirror/scrcpy_record 的长任务并确保文件可播放/窗口关闭; Return: CallToolResult(text + structuredContent); Notes: 按 device.serial 从 sessions 取 Record 会话并调用 scrcpy_close；无会话则跳过；无论成功/失败都会从 sessions 移除避免泄漏。"""

        async def call(device: Device) -> None:
            async with Ins.sessions_lock:
                if not (sess := Ins.sessions.get(device.serial)):
                    return None
            return await sess.scrcpy_close()

        return await broadcast(
            tool="scrcpy_close", args={}, target_list=manage.snapshot, call=call
        )


if __name__ == '__main__':
    pass
