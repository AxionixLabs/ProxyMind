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
          - 多设备同时 pull 到同一 local 不会覆盖/冲突（按 serial 分目录）
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
          - remote 需可写权限
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
          - 仅删除文件（不删目录）
          - 不存在则忽略（rm -f 语义）
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
          - text/data.content: 永远返回摘要（行数受 max_lines 与内部上限共同约束）
          - 过滤顺序：tags+level -> keywords(OR, ignore-case)
          - saved: None 不落盘；非空落盘过滤后全量并返回附件
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
          - 由设备端执行 logcat 清理（不是 rm 文件）
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
