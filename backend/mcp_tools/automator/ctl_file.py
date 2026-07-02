# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.utilities.tool_result import build_tool_result
from backend.mcp_tools.automator.schemas.schema_file import (
    LogKeywordsArg,
    LogLevelArg,
    LogTagsArg,
    MaxLinesArg,
    SavedPathArg
)
from backend.mcp_tools.shared import SerialArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext


def bind(mcp: FastMCP, manage: DeviceManage, _: AppContext) -> None:

    @mcp.tool(
        description=(
            "导出一次过滤后的 logcat 快照。"
            " 该工具会先按 `tags` 和 `level` 从设备侧取日志，再按 `keywords` 做大小写不敏感的 OR 过滤。"
            " `saved` 为空时只返回摘要，非空时会把完整结果落盘并作为附件返回。"
        ),
        meta={"hidden": False, "domain": "device", "class": "file", "supports_parallel": True}
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

        device = await manage.resolve_fresh(serial)
        raw = await device.file_logcat_dump(**args)

        return build_tool_result(tool="file_logcat_dump", args=args, raw=raw, target=device.serial)


if __name__ == '__main__':
    pass
