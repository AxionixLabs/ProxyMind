# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.coding.schemas.schema_native import (
    ShellCommandItemsArg,
    WorkspaceForceArg,
    ApplyPatchArg,
    WorkspaceExpectedSha256MapArg,
    ExecutionMetadataArg,
    shell_command_items_payload
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.tool_result import build_tool_result


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "在工作区内批量执行本地 shell 命令。"
            " items 是命令列表，每项包含 command，可包含 cwd 和 timeout_sec。"
            " 命令必须是 shell 字符串，由系统默认 shell 解释执行。"
            " 适合一次运行多个只读诊断命令，或以单元素 items 运行测试、构建和脚本。"
            " 不要用本工具做工作区文件创建、覆盖或局部修改；代码修改请使用 apply_patch。"
            " 文件复制、移动可通过受控 shell 命令执行。"
            " execution metadata 由服务端策略层补充，用于决定本地执行、云端沙盒或拒绝。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "shell"}
    )
    @task_middleware("shell_command")
    async def shell_command(
        items: ShellCommandItemsArg,
        execution: ExecutionMetadataArg = None
    ) -> CallToolResult:

        args = {
            "items"     : shell_command_items_payload(items),
            "execution" : execution
        }

        job_id = await idle.job_begin("native_coding.shell_command.batch", args=args)
        try:
            raw = await ctx.native_coding.shell_command(**args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="shell_command", args=args, raw=raw, target=ctx.native_coding.agent_id)

    @mcp.tool(
        description=(
            "应用严格 apply_patch 补丁修改工作区文件。"
            " 支持多文件、多 hunk、新建/更新/删除文件、上下文校验、"
            "唯一上下文自动迁移和按文件 SHA256 基线冲突保护。"
            " patch 必须只包含补丁正文：*** Begin Patch、文件操作段和 *** End Patch；"
            "不要带 Markdown、解释文字或缩进前缀。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("apply_patch")
    async def apply_patch(
        patch: ApplyPatchArg,
        expected_sha256: WorkspaceExpectedSha256MapArg = None,
        force: WorkspaceForceArg = False
    ) -> CallToolResult:

        args = {
            "patch"           : patch,
            "expected_sha256" : expected_sha256,
            "force"           : force
        }

        raw = ctx.native_coding.apply_patch(**args)

        return build_tool_result(tool="apply_patch", args=args, raw=raw, target=ctx.native_coding.agent_id)


if __name__ == '__main__':
    pass
