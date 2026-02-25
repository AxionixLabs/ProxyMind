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
          - 采集所有在线设备的状态快照（型号/联网/屏幕/电量等）
          - 并发采集：单设备失败不影响其他设备（失败以 per-device 结果体现）
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
          local: str?  # 由增强层自动传递，无需显示传递
          matrix: dict[str, dict[str, typing.Any]]?
        R: CTR
        N:
          - 多设备时，local 按 serial 分文件名，不会冲突
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
          - 过滤/列出设备已安装包名：
            - keyword 为空：按 scope 列出全部
            - keyword 非空：按关键字（大小写不敏感）过滤
          - scope:
            - user   -> 第三方（用户安装）包（pm list packages -3）
            - system -> 系统包（pm list packages -s）
            - all    -> 全部包（pm list packages）
          - 过滤基于 `pm list packages ... | grep -i <keyword>`（设备侧 grep）
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
