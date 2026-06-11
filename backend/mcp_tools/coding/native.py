# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.coding.schemas.schema_native import (
    WorkspacePathArg,
    WorkspaceOptionalPathArg,
    WorkspacePatternArg,
    WorkspaceContentArg,
    WorkspaceSourcePathArg,
    WorkspaceTargetPathArg,
    WorkspaceSearchQueryArg,
    WorkspaceStartLineArg,
    WorkspaceMaxLinesArg,
    WorkspaceMaxBytesArg,
    WorkspaceCaseSensitiveArg,
    WorkspaceMaxMatchesArg,
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
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
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
            "结构化符号搜索。"
            " 用于查找函数、类、方法、类型等符号定义，并返回相关引用和调用候选。"
            " 文本、错误信息、调用点粗搜和文件路径发现优先使用 shell 中的 rg。"
            " 返回结果包含 path、line、kind、match_count、references、call_candidates 和覆盖诊断等事实字段。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_search")
    async def workspace_search(
        query: WorkspaceSearchQueryArg,
        path: WorkspaceOptionalPathArg = ".",
        glob: WorkspacePatternArg = None,
        case_sensitive: WorkspaceCaseSensitiveArg = False,
        max_matches: WorkspaceMaxMatchesArg = 100
    ) -> CallToolResult:

        args = {
            "query"          : query,
            "path"           : path or ".",
            "glob"           : glob,
            "case_sensitive" : case_sensitive,
            "max_matches"    : max_matches
        }

        async def call(*_) -> dict:
            return ctx.native_coding.search(**args)

        return await broadcast(
            tool="workspace_search",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "并行读取多段工作区上下文。"
            " 只允许 workspace_read_file、workspace_search；"
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
            " 创建或整体覆盖文本文件时优先使用本工具，不要用 shell_exec 的 echo/tee/重定向。"
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
            "移动或重命名工作区内的单个文件。"
            " 适合把根目录或子目录中的文件改名；不会执行 shell，路径不能越过工作区。"
            " 用户要求改名/移动文件时必须优先使用本工具，不要用 shell_exec 执行 mv/move/rename-item。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_move_file")
    async def workspace_move_file(
        source_path: WorkspaceSourcePathArg,
        target_path: WorkspaceTargetPathArg,
        overwrite: WorkspaceOverwriteArg = False,
        create_dirs: WorkspaceCreateDirsArg = True,
        expected_sha256: WorkspaceExpectedSha256Arg = None,
        force: WorkspaceForceArg = False
    ) -> CallToolResult:

        args = {
            "source_path"     : source_path,
            "target_path"     : target_path,
            "overwrite"       : overwrite,
            "create_dirs"     : create_dirs,
            "expected_sha256" : expected_sha256,
            "force"           : force
        }

        async def call(*_) -> dict:
            return ctx.native_coding.move_file(**args)

        return await broadcast(
            tool="workspace_move_file",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "复制工作区内的单个文件到另一个工作区路径。"
            " 路径不能越过工作区；目标存在时默认拒绝覆盖。"
            " 用户要求复制文件时必须优先使用本工具，不要用 shell_exec 执行 cp/copy/copy-item。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_copy_file")
    async def workspace_copy_file(
        source_path: WorkspaceSourcePathArg,
        target_path: WorkspaceTargetPathArg,
        overwrite: WorkspaceOverwriteArg = False,
        create_dirs: WorkspaceCreateDirsArg = True,
        expected_sha256: WorkspaceExpectedSha256Arg = None,
        force: WorkspaceForceArg = False
    ) -> CallToolResult:

        args = {
            "source_path"     : source_path,
            "target_path"     : target_path,
            "overwrite"       : overwrite,
            "create_dirs"     : create_dirs,
            "expected_sha256" : expected_sha256,
            "force"           : force
        }

        async def call(*_) -> dict:
            return ctx.native_coding.copy_file(**args)

        return await broadcast(
            tool="workspace_copy_file",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "删除工作区内的单个文件。"
            " 只删除文件，不删除目录；路径不能越过工作区。"
            " 用户要求删除文件时必须优先使用本工具，不要用 shell_exec 执行 rm/del/remove-item。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_delete_file")
    async def workspace_delete_file(
        path: WorkspacePathArg,
        expected_sha256: WorkspaceExpectedSha256Arg = None,
        force: WorkspaceForceArg = False
    ) -> CallToolResult:

        args = {
            "path"            : path,
            "expected_sha256" : expected_sha256,
            "force"           : force
        }

        async def call(*_) -> dict:
            return ctx.native_coding.delete_file(**args)

        return await broadcast(
            tool="workspace_delete_file",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "对工作区内文本文件执行精确文本替换。"
            " 只有实际匹配次数等于 expected_replacements 时才会写回，避免误改。"
            " 局部修改文本文件时优先使用本工具，不要用 shell_exec 的 sed/perl/重定向改文件。"
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
            " 命令必须使用参数数组，表示真实可执行程序及其参数，不是 shell 字符串。"
            " 适合运行测试、构建、脚本、版本查询和只读诊断命令。"
            " 不要用本工具做工作区文件创建、覆盖、局部修改、删除、复制、移动或重命名；"
            " 文件操作请使用 workspace_write_file、workspace_apply_patch、workspace_apply_unified_patch、"
            "workspace_copy_file、workspace_move_file、workspace_delete_file。"
            " 不要依赖 shell alias/内建命令或 shell 语法，例如 pwd、mv/cp/rm/del/copy/move/dir、管道、重定向、&&。"
            " 执行前必须携带 execution metadata，由执行元数据决定本地执行、云端沙盒或拒绝。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "shell"}
    )
    @task_middleware("shell_exec")
    async def shell_exec(
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
            job_id = await idle.job_begin("native_coding.shell_exec", args=args)
            try:
                return await ctx.native_coding.shell_exec(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="shell_exec",
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
