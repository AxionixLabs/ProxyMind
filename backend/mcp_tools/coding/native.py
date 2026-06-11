# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.coding.schemas.schema_native import (
    WorkspacePathArg,
    WorkspaceOptionalPathArg,
    WorkspaceContentArg,
    WorkspaceStartLineArg,
    WorkspaceMaxLinesArg,
    WorkspaceMaxBytesArg,
    NativeParallelReadItemsArg,
    WorkspaceCreateDirsArg,
    WorkspaceOverwriteArg,
    WorkspaceOldTextArg,
    WorkspaceNewTextArg,
    WorkspaceExpectedReplacementsArg,
    WorkspaceExpectedSha256Arg,
    WorkspaceForceArg,
    WorkspaceUnifiedPatchArg,
    WorkspaceExpectedSha256MapArg,
    ShellCommandArg,
    ShellCwdArg,
    ShellTimeoutArg,
    ExecutionMetadataArg,
    GitDiffMaxCharsArg
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "读取工作区内文本文件。"
            " 支持按起始行和最大行数读取窗口；搜索后优先读取命中附近窗口，不要无条件读取大文件。"
        ),
        meta={"hidden": True, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_read_file")
    async def workspace_read_file(
        path: WorkspacePathArg,
        start_line: WorkspaceStartLineArg = None,
        max_lines: WorkspaceMaxLinesArg = None,
        max_bytes: WorkspaceMaxBytesArg = None
    ) -> CallToolResult:

        args = {
            "path"       : path,
            "start_line" : start_line,
            "max_lines"  : max_lines,
            "max_bytes"  : max_bytes
        }

        async def call(*_) -> dict:
            return ctx.native_coding.read_file(**args)

        return await broadcast(
            tool="workspace_read_file",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "并行读取多段工作区上下文。"
            " 只允许 workspace_read_file；"
            " 不执行 shell、不写文件、不应用 patch。适合一次读取多个搜索候选窗口。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("native_parallel_read")
    async def native_parallel_read(
        items: NativeParallelReadItemsArg
    ) -> CallToolResult:

        args = {
            "items" : items
        }

        async def call(*_) -> dict:
            return await ctx.native_coding.parallel_read(**args)

        return await broadcast(
            tool="native_parallel_read",
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
            "对工作区内文本文件执行精确文本替换。"
            " 只有实际匹配次数等于 expected_replacements 时才会写回，避免误改。"
            " 局部修改文本文件时优先使用本工具，不要用 shell_command 的 sed/perl/重定向改文件。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_apply_patch")
    async def workspace_apply_patch(
        path: WorkspacePathArg,
        old_text: WorkspaceOldTextArg,
        new_text: WorkspaceNewTextArg,
        expected_replacements: WorkspaceExpectedReplacementsArg = 1,
        expected_sha256: WorkspaceExpectedSha256Arg = None,
        force: WorkspaceForceArg = False
    ) -> CallToolResult:

        args = {
            "path"                  : path,
            "old_text"              : old_text,
            "new_text"              : new_text,
            "expected_replacements" : expected_replacements,
            "expected_sha256"       : expected_sha256,
            "force"                 : force
        }

        async def call(*_) -> dict:
            return ctx.native_coding.apply_patch(**args)

        return await broadcast(
            tool="workspace_apply_patch",
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

    @mcp.tool(
        description=(
            "在工作区内执行一次本地命令。"
            " 命令必须是 shell 字符串，由系统默认 shell 解释执行。"
            " 适合运行测试、构建、脚本、版本查询和诊断命令。"
            " 不要用本工具做工作区文件创建、覆盖或局部修改；"
            " 文本写入请使用 workspace_write_file、workspace_apply_patch 或 workspace_apply_unified_patch。"
            " 文件复制、移动、删除可通过受控 shell 命令执行。"
            " 执行前必须携带 execution metadata，由执行元数据决定本地执行、云端沙盒或拒绝。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "shell"}
    )
    @task_middleware("shell_command")
    async def shell_command(
        command: ShellCommandArg,
        cwd: ShellCwdArg = ".",
        timeout_sec: ShellTimeoutArg = 60,
        execution: ExecutionMetadataArg = None
    ) -> CallToolResult:

        args = {
            "command"         : command,
            "cwd"             : cwd,
            "timeout_sec"     : timeout_sec,
            "execution"       : execution
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin("native_coding.shell_command", args=args)
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
        description="返回当前工作区的 `git status --short`。",
        meta={"hidden": False, "domain": "coding", "class": "git"}
    )
    @task_middleware("git_status")
    async def git_status() -> CallToolResult:

        async def call(*_) -> dict:
            return await ctx.native_coding.git_status()

        return await broadcast(
            tool="git_status",
            args={},
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "返回当前工作区 git diff。"
            " 可选 path 用于限制到单个路径，输出会按 max_chars 截断。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "git"}
    )
    @task_middleware("git_diff")
    async def git_diff(
        path: WorkspaceOptionalPathArg = None,
        max_chars: GitDiffMaxCharsArg = 24000
    ) -> CallToolResult:

        args = {
            "path"      : path,
            "max_chars" : max_chars
        }

        async def call(*_) -> dict:
            return await ctx.native_coding.git_diff(**args)

        return await broadcast(
            tool="git_diff",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "生成提交前/最终回答前的变更摘要和质量闸。"
            " 汇总当前 git_status、diff 统计、未跟踪文件预览、冲突和截断风险，"
            "并返回 verification.sufficient 判断当前工作区状态是否存在阻断项。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "git"}
    )
    @task_middleware("change_summary")
    async def change_summary(
        max_diff_chars: GitDiffMaxCharsArg = 12000
    ) -> CallToolResult:

        args = {
            "max_diff_chars" : max_diff_chars
        }

        async def call(*_) -> dict:
            return await ctx.native_coding.change_summary(**args)

        return await broadcast(
            tool="change_summary",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
