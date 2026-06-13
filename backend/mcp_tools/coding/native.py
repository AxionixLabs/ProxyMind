# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.coding.schemas.schema_native import (
    WorkspacePathArg,
    WorkspaceContentArg,
    ShellCommandItemsArg,
    WorkspaceCreateDirsArg,
    WorkspaceOverwriteArg,
    WorkspaceExpectedSha256Arg,
    WorkspaceForceArg,
    WorkspaceUnifiedPatchArg,
    WorkspaceExpectedSha256MapArg,
    ExecutionMetadataArg,
    shell_command_items_payload
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "在工作区内批量执行本地 shell 命令。"
            " items 是命令列表，每项包含 command，可包含 cwd 和 timeout_sec。"
            " 命令必须是 shell 字符串，由系统默认 shell 解释执行。"
            " 适合一次运行多个只读诊断命令，或以单元素 items 运行测试、构建和脚本。"
            " 不要用本工具做工作区文件创建、覆盖或局部修改；"
            " 文本写入和工作区文件删除请使用 workspace_write_file 或 workspace_apply_unified_patch。"
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

        async def call(*_) -> dict:
            job_id = await idle.job_begin("native_coding.shell_command.batch", args=args)
            try:
                return await ctx.native_coding.shell_command(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="shell_command",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "写入工作区内文本文件。"
            " 默认允许覆盖并自动创建父目录，路径不能越过工作区。"
            " 创建或整体覆盖文本文件时优先使用本工具，不要用 shell_command 的 echo/tee/重定向。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_write_file")
    async def workspace_write_file(
        path: WorkspacePathArg,
        content: WorkspaceContentArg,
        create_dirs: WorkspaceCreateDirsArg = True,
        overwrite: WorkspaceOverwriteArg = True,
        expected_sha256: WorkspaceExpectedSha256Arg = None,
        force: WorkspaceForceArg = False
    ) -> CallToolResult:

        args = {
            "path"            : path,
            "content"         : content,
            "create_dirs"     : create_dirs,
            "overwrite"       : overwrite,
            "expected_sha256" : expected_sha256,
            "force"           : force
        }

        async def call(*_) -> dict:
            return ctx.native_coding.write_file(**args)

        return await broadcast(
            tool="workspace_write_file",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "应用 unified diff patch。"
            " 支持多文件、多 hunk、新建/删除文件、上下文校验、"
            "唯一上下文自动迁移和按文件 SHA256 基线冲突保护。"
            " patch 必须是原始 unified diff，包含 ---/+++ 文件头；不要带 UI 行号、Markdown、解释文字或缩进前缀。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_apply_unified_patch")
    async def workspace_apply_unified_patch(
        patch: WorkspaceUnifiedPatchArg,
        expected_sha256: WorkspaceExpectedSha256MapArg = None,
        force: WorkspaceForceArg = False
    ) -> CallToolResult:

        args = {
            "patch"           : patch,
            "expected_sha256" : expected_sha256,
            "force"           : force
        }

        async def call(*_) -> dict:
            return ctx.native_coding.apply_unified_patch(**args)

        return await broadcast(
            tool="workspace_apply_unified_patch",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
