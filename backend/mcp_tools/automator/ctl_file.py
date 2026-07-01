# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_tools.automator.schemas.schema_file import (
    DevicePathArg,
    LocalPathArg,
    LogKeywordsArg,
    LogLevelArg,
    LogTagsArg,
    MaxLinesArg,
    RemotePathArg,
    SavedPathArg
)
from backend.mcp_tools.shared import MatrixArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, manage: DeviceManage, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "从设备拉取单个文件到本地。"
            " 多设备同时执行时会按设备 serial 区分落盘路径，避免互相覆盖。"
            " 远端路径不存在或本地目标不可写时会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "file"}
    )
    @task_middleware("file_pull")
    async def file_pull(
        remote: RemotePathArg,
        local: LocalPathArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "把本地文件推送到设备指定路径。"
            " 该工具只负责文件传输，不会自动创建缺失父目录或校验目标文件用途。"
            " 本地文件不存在或设备目标路径不可写时会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "file"}
    )
    @task_middleware("file_push")
    async def file_push(
        local: LocalPathArg,
        remote: RemotePathArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "删除设备上的单个文件路径。"
            " 该工具不递归删除目录，目标不存在时按忽略处理。"
            " 适合清理单个产物或临时文件，不适合做目录级清场。"
        ),
        meta={"hidden": False, "domain": "device", "class": "file"}
    )
    @task_middleware("file_remove")
    async def file_remove(
        path: DevicePathArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "导出一次过滤后的 logcat 快照。"
            " 该工具会先按 `tags` 和 `level` 从设备侧取日志，再按 `keywords` 做大小写不敏感的 OR 过滤。"
            " `saved` 为空时只返回摘要，非空时会把完整结果落盘并作为附件返回。"
        ),
        meta={"hidden": False, "domain": "device", "class": "file"}
    )
    @task_middleware("file_logcat_dump")
    async def file_logcat_dump(
        keywords: LogKeywordsArg = None,
        tags: LogTagsArg = None,
        level: LogLevelArg = "W",
        max_lines: MaxLinesArg = 200,
        saved: SavedPathArg = None,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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


if __name__ == '__main__':
    pass
