# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.coding.schemas.schema_native import (
    WorkspacePathArg,
    WorkspaceOptionalPathArg,
    WorkspacePatternArg,
    WorkspaceRecursiveArg,
    WorkspaceMaxItemsArg,
    WorkspaceContentArg,
    WorkspaceSourcePathArg,
    WorkspaceTargetPathArg,
    WorkspaceQueryArg,
    WorkspaceStartLineArg,
    WorkspaceMaxLinesArg,
    WorkspaceMaxBytesArg,
    WorkspaceCaseSensitiveArg,
    WorkspaceMaxMatchesArg,
    NativeParallelReadItemsArg,
    RepoMapMaxFilesArg,
    RepoMapMaxSymbolsArg,
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
    ShellAllowDangerousArg,
    ShellAllowReviewArg,
    GitDiffMaxCharsArg,
    NativeLoopPromptArg,
    NativeLoopStepsArg,
    NativeRepairStepsArg,
    NativeLoopVerifyCommandArg,
    NativeLoopStopOnFailArg,
    NativeLoopMaxStepsArg,
    NativeLoopAutoRepairArg,
    NativeLoopAutoRollbackArg,
    NativeSessionIdArg,
    NativeRequiredSessionIdArg,
    NativeRunIdArg,
    NativePlanActionArg,
    NativePlanTodosArg,
    NativePlanStringsArg,
    NativePlanNoteArg,
    NativePlanModeArg,
    NativePlanUpdateArg,
    SandboxExitCodeArg,
    SandboxOutputArg,
    SandboxElapsedArg,
    SandboxTimedOutArg,
    SandboxProviderArg,
    SandboxFileChangesArg,
    SandboxArtifactsArg,
    SandboxVerifyArg
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "返回当前原生编码工作区根目录。"
            " 该工具用于确认后续 workspace、shell、git 工具的路径边界。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_root")
    async def workspace_root() -> CallToolResult:

        async def call(*_) -> dict:
            return ctx.native_coding.workspace_root()

        return await broadcast(
            tool="workspace_root",
            args={},
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "列出工作区内文件或目录。"
            " 默认递归列出，自动跳过 .git、venv、node_modules 等重型目录。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_list_files")
    async def workspace_list_files(
        path: WorkspaceOptionalPathArg = ".",
        pattern: WorkspacePatternArg = None,
        recursive: WorkspaceRecursiveArg = True,
        max_items: WorkspaceMaxItemsArg = 200
    ) -> CallToolResult:

        args = {
            "path"      : path or ".",
            "pattern"   : pattern,
            "recursive" : recursive,
            "max_items" : max_items
        }

        async def call(*_) -> dict:
            return ctx.native_coding.list_files(**args)

        return await broadcast(
            tool="workspace_list_files",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "读取工作区内文本文件。"
            " 支持按起始行和最大行数切片，大文件会按字节上限截断。"
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
            "在工作区文本文件中搜索字符串。"
            " 返回文件路径、行号和匹配行摘要，适合编码任务定位上下文。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("workspace_search_text")
    async def workspace_search_text(
        query: WorkspaceQueryArg,
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
            return ctx.native_coding.search_text(**args)

        return await broadcast(
            tool="workspace_search_text",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "并行读取多段工作区上下文。"
            " 只允许 workspace_root、workspace_list_files、workspace_read_file、workspace_search_text；"
            " 不执行 shell、不写文件、不应用 patch。"
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
            "生成轻量 repo map / 符号索引。"
            " 扫描 Python、TypeScript/JavaScript、Go、Rust 常见定义和 imports，"
            "用于跨文件定位和修改前理解代码结构。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("repo_map")
    async def repo_map(
        path: WorkspaceOptionalPathArg = ".",
        glob: WorkspacePatternArg = None,
        max_files: RepoMapMaxFilesArg = 200,
        max_symbols: RepoMapMaxSymbolsArg = 1000
    ) -> CallToolResult:

        args = {
            "path"        : path or ".",
            "glob"        : glob,
            "max_files"   : max_files,
            "max_symbols" : max_symbols
        }

        async def call(*_) -> dict:
            return ctx.native_coding.repo_map(**args)

        return await broadcast(
            tool="repo_map",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "在轻量 repo map 中按名称查找符号定义。"
            " 返回匹配的函数、类、方法、类型等定义位置。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "workspace"}
    )
    @task_middleware("repo_find_symbol")
    async def repo_find_symbol(
        query: WorkspaceQueryArg,
        path: WorkspaceOptionalPathArg = ".",
        glob: WorkspacePatternArg = None,
        max_matches: WorkspaceMaxMatchesArg = 50
    ) -> CallToolResult:

        args = {
            "query"       : query,
            "path"        : path or ".",
            "glob"        : glob,
            "max_matches" : max_matches
        }

        async def call(*_) -> dict:
            return ctx.native_coding.find_symbol(**args)

        return await broadcast(
            tool="repo_find_symbol",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "写入工作区内文本文件。"
            " 默认允许覆盖并自动创建父目录，路径不能越过工作区。"
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
            "对工作区内文本文件执行精确文本替换。"
            " 只有实际匹配次数等于 expected_replacements 时才会写回，避免误改。"
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
            " 命令必须使用参数数组；项目测试/只读命令可直接执行。"
            " shell 控制符、常见写文件命令、危险命令、依赖安装、网络下载和 git 写操作会进入审批流程；"
            " 用户要求执行这类操作时仍应调用本工具，由客户端和服务端完成审批，不要改为让用户手动执行。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "shell"}
    )
    @task_middleware("shell_exec")
    async def shell_exec(
        command: ShellCommandArg,
        cwd: ShellCwdArg = ".",
        timeout_sec: ShellTimeoutArg = 60,
        allow_review: ShellAllowReviewArg = False,
        allow_dangerous: ShellAllowDangerousArg = False
    ) -> CallToolResult:

        args = {
            "command"         : command,
            "cwd"             : cwd,
            "timeout_sec"     : timeout_sec,
            "allow_review"    : allow_review,
            "allow_dangerous" : allow_dangerous
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
            " 汇总 git status、diff 统计、未跟踪文件、冲突、最近 native session 的 preflight/verify 状态。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "git"}
    )
    @task_middleware("change_summary")
    async def change_summary(
        session_id: NativeSessionIdArg = None,
        max_diff_chars: GitDiffMaxCharsArg = 12000
    ) -> CallToolResult:

        args = {
            "session_id"     : session_id,
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

    @mcp.tool(
        description=(
            "按 native coding session/run 的文件快照回滚本轮改动。"
            " 只恢复工具记录过的文件快照，不执行 git reset。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("rollback_run")
    async def rollback_run(
        session_id: NativeRequiredSessionIdArg,
        run_id: NativeRunIdArg = None
    ) -> CallToolResult:

        args = {
            "session_id" : session_id,
            "run_id"     : run_id
        }

        async def call(*_) -> dict:
            return ctx.native_coding.rollback_run(**args)

        return await broadcast(
            tool="rollback_run",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "读取或更新 native coding session 的计划状态。"
            " 可记录 todos、assumptions、next_steps 和 notes，辅助多轮编码任务管理。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("native_plan")
    async def native_plan(
        action: NativePlanActionArg = "get",
        session_id: NativeSessionIdArg = None,
        todos: NativePlanTodosArg = None,
        assumptions: NativePlanStringsArg = None,
        next_steps: NativePlanStringsArg = None,
        note: NativePlanNoteArg = None,
        mode: NativePlanModeArg = "merge"
    ) -> CallToolResult:

        args = {
            "action"      : action,
            "session_id"  : session_id,
            "todos"       : todos,
            "assumptions" : assumptions,
            "next_steps"  : next_steps,
            "note"        : note,
            "mode"        : mode
        }

        async def call(*_) -> dict:
            if str(action or "get").strip().lower() == "update":
                return ctx.native_coding.update_plan(
                    session_id=session_id,
                    todos=todos,
                    assumptions=assumptions,
                    next_steps=next_steps,
                    note=note,
                    mode=mode
                )
            return ctx.native_coding.get_plan(session_id=session_id)

        return await broadcast(
            tool="native_plan",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次原生编码步骤循环。"
            " 只允许调用 workspace、shell 和 git 白名单步骤，执行后返回步骤结果、"
            "验证结果、验证失败诊断、自动读取的错误上下文、repair_prompt、建议读取步骤和 diff。"
            " 执行前会预检 tool、参数、路径、sha256、patch 和命令风险；"
            "验证失败诊断可识别 pytest、tsc、jest/vitest、ruff/mypy、go test、cargo test 常见输出；"
            "auto_repair=true/plan 时会生成 repair_plan，供外层 chat/fast/xtra 继续生成 patch steps 并调用工具执行；"
            "auto_rollback 可在步骤失败或验证失败后按 run 快照自动恢复文件；"
            "传入已有 session_id 会追加新的 run，并保留完整修复轨迹。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("native_coding_loop")
    async def native_coding_loop(
        prompt: NativeLoopPromptArg,
        steps: NativeLoopStepsArg = None,
        verify_command: NativeLoopVerifyCommandArg = None,
        stop_on_fail: NativeLoopStopOnFailArg = True,
        max_steps: NativeLoopMaxStepsArg = 20,
        session_id: NativeSessionIdArg = None,
        plan_update: NativePlanUpdateArg = None,
        auto_repair: NativeLoopAutoRepairArg = False,
        auto_rollback: NativeLoopAutoRollbackArg = False
    ) -> CallToolResult:

        args = {
            "prompt"         : prompt,
            "steps"          : steps,
            "verify_command" : verify_command,
            "stop_on_fail"   : stop_on_fail,
            "max_steps"      : max_steps,
            "session_id"     : session_id,
            "plan_update"    : plan_update,
            "auto_repair"    : auto_repair,
            "auto_rollback"  : auto_rollback
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin("native_coding.loop", args=args)
            try:
                return await ctx.native_coding.native_loop(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="native_coding_loop",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "执行一次原生修复闭环。"
            " 输入外层模型生成的 repair steps，先校验修复步骤白名单，"
            "再执行 read/patch 步骤，并把最后一个 shell_exec 作为验证命令运行；"
            "验证失败时继续生成 diagnostics/repair_plan，可按 run 快照自动回滚。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("native_repair_loop")
    async def native_repair_loop(
        session_id: NativeRequiredSessionIdArg,
        steps: NativeRepairStepsArg,
        source_run_id: NativeRunIdArg = None,
        stop_on_fail: NativeLoopStopOnFailArg = True,
        max_steps: NativeLoopMaxStepsArg = 20,
        auto_repair: NativeLoopAutoRepairArg = False,
        auto_rollback: NativeLoopAutoRollbackArg = "verify_failed"
    ) -> CallToolResult:

        args = {
            "session_id"     : session_id,
            "steps"          : steps,
            "source_run_id"  : source_run_id,
            "stop_on_fail"   : stop_on_fail,
            "max_steps"      : max_steps,
            "auto_repair"    : auto_repair,
            "auto_rollback"  : auto_rollback
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin("native_coding.repair_loop", args=args)
            try:
                return await ctx.native_coding.native_repair_loop(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="native_repair_loop",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "把外层云端 sandbox 的命令执行结果回填到 native coding session。"
            " verify=true 时会作为验证结果触发 diagnostics/repair_plan，便于继续修复闭环。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("record_sandbox_result")
    async def record_sandbox_result(
        session_id: NativeRequiredSessionIdArg,
        command: ShellCommandArg,
        cwd: ShellCwdArg = ".",
        exit_code: SandboxExitCodeArg = 0,
        stdout: SandboxOutputArg = "",
        stderr: SandboxOutputArg = "",
        elapsed_ms: SandboxElapsedArg = None,
        timed_out: SandboxTimedOutArg = False,
        sandbox_provider: SandboxProviderArg = "cloud_sandbox",
        file_changes: SandboxFileChangesArg = None,
        artifacts: SandboxArtifactsArg = None,
        verify: SandboxVerifyArg = False,
        auto_repair: NativeLoopAutoRepairArg = False,
        run_id: NativeRunIdArg = None
    ) -> CallToolResult:

        args = {
            "session_id"       : session_id,
            "command"          : command,
            "cwd"              : cwd,
            "exit_code"        : exit_code,
            "stdout"           : stdout,
            "stderr"           : stderr,
            "elapsed_ms"       : elapsed_ms,
            "timed_out"        : timed_out,
            "sandbox_provider" : sandbox_provider,
            "file_changes"     : file_changes,
            "artifacts"        : artifacts,
            "verify"           : verify,
            "auto_repair"      : auto_repair,
            "run_id"           : run_id
        }

        async def call(*_) -> dict:
            return ctx.native_coding.record_sandbox_result(**args)

        return await broadcast(
            tool="record_sandbox_result",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "查询原生编码会话摘要。"
            " session_id 为空时返回最近会话列表；非空时返回指定会话详情。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("native_coding_session")
    async def native_coding_session(
        session_id: NativeSessionIdArg = None
    ) -> CallToolResult:

        args = {
            "session_id" : session_id
        }

        async def call(*_) -> dict:
            return ctx.native_coding.session_snapshot(session_id=session_id)

        return await broadcast(
            tool="native_coding_session",
            args=args,
            target_list=[ctx.native_coding],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
