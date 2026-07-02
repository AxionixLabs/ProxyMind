# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.utilities.tool_result import build_tool_result
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
from backend.mcp_tools.shared import SerialArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext


def bind(mcp: FastMCP, manage: DeviceManage, _: AppContext) -> None:

    @mcp.tool(
        description=(
            "从设备拉取单个文件到本地。"
            " 多设备连接时应通过 `serial` 指定目标设备。"
            " 远端路径不存在或本地目标不可写时会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "file"}
    )
    @task_middleware("file_pull")
    async def file_pull(
        remote: RemotePathArg,
        local: LocalPathArg,
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "remote" : remote,
            "local"  : local
        }

        device = manage.resolve(serial)
        raw = await device.file_pull(**args)

        return build_tool_result(tool="file_pull", args=args, raw=raw, target=device.serial)

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
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "local"  : local,
            "remote" : remote
        }

        device = manage.resolve(serial)
        raw = await device.file_push(**args)

        return build_tool_result(tool="file_push", args=args, raw=raw, target=device.serial)

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
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "path" : path
        }

        device = manage.resolve(serial)
        raw = await device.file_remove(**args)

        return build_tool_result(tool="file_remove", args=args, raw=raw, target=device.serial)

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
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "keywords"  : keywords,
            "tags"      : tags,
            "level"     : level,
            "max_lines" : max_lines,
            "saved"     : saved
        }

        device = manage.resolve(serial)
        raw = await device.file_logcat_dump(**args)

        return build_tool_result(tool="file_logcat_dump", args=args, raw=raw, target=device.serial)


if __name__ == '__main__':
    pass
