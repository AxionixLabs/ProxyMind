#   ____ _____ _       _____ _ _
#  / ___|_   _| |     |  ___(_) | ___
# | |     | | | |     | |_  | | |/ _ \
# | |___  | | | |___  |  _| | | |  __/
#  \____| |_| |_____| |_|   |_|_|\___|
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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "file"})
    @task_middleware("file_pull")
    async def file_pull(
        remote: str,
        local: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: file
        A: file_pull
        P:
          remote: str
          local: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 从设备拉取单个文件到本地。
          - 多设备同时执行时会按设备 serial 自动区分落盘路径，避免互相覆盖。
        """

        args = {
            "remote" : remote,
            "local"  : local
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.file_pull(**a)

        return await broadcast(
            tool="file_pull",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "file"})
    @task_middleware("file_push")
    async def file_push(
        local: str,
        remote: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: file
        A: file_push
        P:
          local: str
          remote: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 把本地文件推送到设备指定路径。
          - 要求本地文件存在，且 remote 所在位置对 adb shell 具备写权限。
        """

        args = {
            "local"  : local,
            "remote" : remote
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.file_push(**a)

        return await broadcast(
            tool="file_push",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "file"})
    @task_middleware("file_remove")
    async def file_remove(
        path: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: file
        A: file_remove
        P:
          path: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 删除设备上的单个文件路径。
          - 不递归删除目录；目标不存在时按忽略处理。
        """

        args = {
            "path" : path
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.file_remove(**a)

        return await broadcast(
            tool="file_remove",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "file"})
    @task_middleware("file_logcat_dump")
    async def file_logcat_dump(
        keywords: typing.Optional[list[str]] = None,
        tags: typing.Optional[list[str]] = None,
        level: str = "W",
        max_lines: int = 200,
        saved: typing.Optional[str] = None,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: file
        A: file_logcat_dump
        P:
          keywords: list[str]?=None
          tags: list[str]?=None
          level: str="W"  # V/D/I/W/E/F/S
          max_lines: int=200
          saved: str?=None
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 导出一次过滤后的 logcat 快照。
          - 先按 tags 和 level 从设备侧取日志，再按 keywords 做大小写不敏感的 OR 过滤。
          - text 和 data.content 返回摘要内容，摘要行数受 max_lines 与内部上限共同约束。
          - saved=None 时只返回摘要；saved 非空时会把过滤后的完整结果落盘并作为附件返回。
        """
    
        args = {
            "keywords"  : keywords,
            "tags"      : tags,
            "level"     : level,
            "max_lines" : max_lines,
            "saved"     : saved
        }
    
        async def call(device: Device, a: dict) -> typing.Any:
            return await device.file_logcat_dump(**a)
    
        return await broadcast(
            tool="file_logcat_dump",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "file"})
    @task_middleware("file_logcat_clean")
    async def file_logcat_clean(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: file
        A: file_logcat_clean
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 清空设备当前 logcat 缓冲区。
          - 这是日志缓冲清理，不是删除磁盘日志文件。
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.file_logcat_clean()

        return await broadcast(
            tool="file_logcat_clean",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
