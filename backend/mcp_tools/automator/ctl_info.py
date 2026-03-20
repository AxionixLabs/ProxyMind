#   ____ _____ _       ___        __
#  / ___|_   _| |     |_ _|_ __  / _| ___
# | |     | | | |      | || '_ \| |_ / _ \
# | |___  | | | |___   | || | | |  _| (_) |
#  \____| |_| |_____| |___|_| |_|_|  \___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "info"})
    @task_middleware("device_snapshot")
    async def device_snapshot(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: info
        A: device_snapshot
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 采集设备当前状态快照，包括基础属性、联网状态、屏幕状态和电量等信息。
          - 多设备场景下逐台并发采集，单台失败不会阻断其他设备。
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.device_snapshot()

        return await broadcast(
            tool="device_snapshot",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "info"})
    @task_middleware("screenshot")
    async def screenshot(
        local: typing.Optional[str] = None,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: info
        A: screenshot
        P:
          local: str?  # 可选。本地保存目录或基准路径
          matrix: dict[str, dict[str, typing.Any]]?
        R: CTR
        N:
          - 截取设备当前屏幕并返回本地附件路径。
          - 提供 local 时作为保存目录或基准路径使用；多设备执行时会按设备 serial 自动区分文件名。
        """

        args = {
            "local" : local
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.screenshot(**a)

        return await broadcast(
            tool="screenshot",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "info"})
    @task_middleware("grep_packages")
    async def grep_packages(
        keyword: typing.Optional[str] = None,
        scope: typing.Literal["user", "system", "all"] = "user",
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: info
        A: grep_packages
        P:
          keyword: str?=None
          scope: 'user'|'system'|'all'='user'
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 列出设备上已安装的包名。
          - keyword 为空时按 scope 返回整类包；keyword 非空时做大小写不敏感的包含过滤。
          - scope=user 仅第三方包，scope=system 仅系统包，scope=all 返回全部包。
        """

        args = {
            "keyword" : keyword,
            "scope"   : scope
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.grep_packages(**a)

        return await broadcast(
            tool="grep_packages",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
