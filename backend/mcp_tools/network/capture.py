# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import (
    DeviceManage,
    Requires
)
from backend.mcp_tools.shared import (
    PackageArg,
    SerialArg
)
from backend.mcp_tools.network.schemas.schema_capture import (
    ProxyHostArg,
    CaptureIdArg,
    HostArg,
    PathArg,
    MethodArg,
    StatusCodeArg,
    LimitArg,
    ExportFormatArg,
    RuleArg
)
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import (
    AppContext,
    Idle
)
from backend.utilities.tool_result import build_tool_result


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle, ctx: AppContext) -> None:
    """注册网络抓包工具。"""

    @mcp.tool(
        description="为目标 Android 设备启动 mitmproxy 抓包会话，并将设备全局 HTTP 代理指向 Helix。包名用于启动和标记会话，不代表系统级按包隔离。",
        meta={"hidden": False, "domain": "network", "class": "capture"},
    )
    @task_middleware("capture_start")
    async def capture_start(
        package_name: PackageArg,
        serial: SerialArg = None,
        *,
        proxy_host: ProxyHostArg = None
    ) -> CallToolResult:

        await Requires.connect_mitmdump()

        args = {
            "package_name" : package_name,
            "proxy_host"   : proxy_host
        }

        device = await manage.resolve_fresh(serial)
        raw = await ctx.capture.start(device=device, package_name=package_name, idle=idle, proxy_host=proxy_host)

        return build_tool_result(tool="capture_start", args=args, raw=raw, target=device.serial)

    @mcp.tool(
        description="停止指定抓包会话并恢复设备此前的全局 HTTP 代理。若代理在抓包期间被外部修改，工具不会覆盖该新设置。",
        meta={"hidden": False, "domain": "network", "class": "capture"},
    )
    @task_middleware("capture_stop")
    async def capture_stop(capture_id: CaptureIdArg) -> CallToolResult:
        raw = await ctx.capture.stop(capture_id)
        return build_tool_result(tool="capture_stop", args={"capture_id": capture_id}, raw=raw, target=capture_id)

    @mcp.tool(
        description="查询抓包会话中的完整 HTTP 流记录。路径按片段匹配；结果包含请求和响应的 headers、query 与 body。",
        meta={"hidden": False, "domain": "network", "class": "capture"},
    )
    @task_middleware("capture_query")
    async def capture_query(
        capture_id: CaptureIdArg,
        *,
        host: HostArg = None,
        path: PathArg = None,
        method: MethodArg = None,
        status_code: StatusCodeArg = None,
        limit: LimitArg = 50
    ) -> CallToolResult:

        args = {
            "capture_id"  : capture_id,
            "host"        : host,
            "path"        : path,
            "method"      : method,
            "status_code" : status_code,
            "limit"       : limit
        }

        job_id = await idle.job_begin("capture.capture_query", args=args)

        try:
            raw = ctx.capture.query(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="capture_query", args=args, raw=raw, target=capture_id)

    @mcp.tool(
        description="将抓包会话导出为完整 HAR 或 JSON 文件。",
        meta={"hidden": False, "domain": "network", "class": "capture"},
    )
    @task_middleware("capture_export")
    async def capture_export(
        capture_id: CaptureIdArg,
        export_format: ExportFormatArg = "har"
    ) -> CallToolResult:

        args = {
            "capture_id"    : capture_id,
            "export_format" : export_format
        }

        job_id = await idle.job_begin("capture.capture_export", args=args)

        try:
            raw = ctx.capture.export(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="capture_export", args=args, raw=raw, target=capture_id)

    @mcp.tool(
        description="按结构化规则断言抓包结果。可约束主机、路径、方法、状态码、数量和最大耗时。",
        meta={"hidden": False, "domain": "network", "class": "capture"},
    )
    @task_middleware("capture_assert")
    async def capture_assert(
        capture_id: CaptureIdArg,
        rule: RuleArg
    ) -> CallToolResult:

        args = {
            "capture_id" : capture_id,
            "rule"       : rule
        }

        job_id = await idle.job_begin("capture.capture_assert", args=args)

        try:
            raw = ctx.capture.assert_rule(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="capture_assert", args=args, raw=raw, target=capture_id)


if __name__ == "__main__":
    pass
