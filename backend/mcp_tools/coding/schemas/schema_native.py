# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


WorkspacePathArg = typing.Annotated[
    str,
    Field(description="工作区内相对路径；不允许越过工作区根目录。"),
]
WorkspaceOptionalPathArg = typing.Annotated[
    typing.Optional[str],
    Field(description="工作区内可选相对路径；为空时使用工作区根目录。"),
]
WorkspacePatternArg = typing.Annotated[
    typing.Optional[str],
    Field(description="文件名或相对路径 glob 过滤表达式。"),
]
WorkspaceRecursiveArg = typing.Annotated[
    bool,
    Field(description="是否递归列出子目录。"),
]
WorkspaceMaxItemsArg = typing.Annotated[
    int,
    Field(description="最多返回的文件项数量，工具内部会限制上限。"),
]
WorkspaceContentArg = typing.Annotated[
    str,
    Field(description="要写入文件的完整文本内容。"),
]
WorkspaceSourcePathArg = typing.Annotated[
    str,
    Field(description="工作区内要移动或重命名的源文件相对路径；不允许越过工作区根目录。"),
]
WorkspaceTargetPathArg = typing.Annotated[
    str,
    Field(description="工作区内移动或重命名后的目标文件相对路径；不允许越过工作区根目录。"),
]
WorkspaceQueryArg = typing.Annotated[
    str,
    Field(description="要在工作区文本文件中查找的字符串。"),
]
WorkspaceStartLineArg = typing.Annotated[
    typing.Optional[int],
    Field(description="读取文件时的起始行号，从 1 开始。"),
]
WorkspaceMaxLinesArg = typing.Annotated[
    typing.Optional[int],
    Field(description="读取文件时最多返回的行数。"),
]
WorkspaceMaxBytesArg = typing.Annotated[
    typing.Optional[int],
    Field(description="读取文件时最多读取的字节数。"),
]
WorkspaceCaseSensitiveArg = typing.Annotated[
    bool,
    Field(description="文本搜索是否区分大小写。"),
]
WorkspaceMaxMatchesArg = typing.Annotated[
    int,
    Field(description="文本搜索最多返回的匹配条数。"),
]
NativeParallelReadItemsArg = typing.Annotated[
    list[dict[str, typing.Any]],
    Field(
        description=(
            "并行读取上下文的只读步骤列表。每项包含 tool 和 args；"
            "仅允许 workspace_root、workspace_list_files、workspace_read_file、workspace_search_text。"
        )
    ),
]
RepoMapMaxFilesArg = typing.Annotated[
    int,
    Field(description="repo map 最多扫描的文件数量。"),
]
RepoMapMaxSymbolsArg = typing.Annotated[
    int,
    Field(description="repo map 最多返回的符号数量。"),
]
WorkspaceCreateDirsArg = typing.Annotated[
    bool,
    Field(description="写文件时是否自动创建父目录。"),
]
WorkspaceOverwriteArg = typing.Annotated[
    bool,
    Field(description="目标文件已存在时是否允许覆盖。"),
]
WorkspaceOldTextArg = typing.Annotated[
    str,
    Field(description="文本替换 patch 的原始片段。"),
]
WorkspaceNewTextArg = typing.Annotated[
    str,
    Field(description="文本替换 patch 的新片段。"),
]
WorkspaceExpectedReplacementsArg = typing.Annotated[
    int,
    Field(description="期望替换次数；实际次数不一致时拒绝修改。"),
]
WorkspaceExpectedSha256Arg = typing.Annotated[
    typing.Optional[str],
    Field(description="编辑前文件 SHA256 基线；当前文件不匹配时拒绝写入，避免覆盖外部改动。"),
]
WorkspaceForceArg = typing.Annotated[
    bool,
    Field(description="是否跳过 SHA256 基线冲突检查。仅在明确需要覆盖外部改动时使用。"),
]
WorkspaceUnifiedPatchArg = typing.Annotated[
    str,
    Field(description="标准 unified diff 文本，支持多文件、多 hunk、新建/删除文件和唯一上下文自动迁移。"),
]
WorkspaceExpectedSha256MapArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="按文件路径映射的 SHA256 基线；当前文件不匹配时拒绝应用 patch。"),
]

ShellCommandArg = typing.Annotated[
    list[str],
    Field(description="以参数数组表达的命令；不接受 shell 控制符拼接。"),
]
ShellCwdArg = typing.Annotated[
    str,
    Field(description="命令工作目录，必须在当前工作区内。"),
]
ShellTimeoutArg = typing.Annotated[
    int,
    Field(description="命令超时秒数。"),
]
ShellAllowDangerousArg = typing.Annotated[
    bool,
    Field(description="审批通过后由服务端设置，用于继续执行需要该标记的命令；默认 false。"),
]
ShellAllowReviewArg = typing.Annotated[
    bool,
    Field(description="审批通过后由服务端设置，用于继续执行安装依赖、网络下载或 git 写操作等命令；默认 false。"),
]

GitDiffMaxCharsArg = typing.Annotated[
    int,
    Field(description="git diff 最多返回的字符数。"),
]

NativeLoopPromptArg = typing.Annotated[
    str,
    Field(description="原生编码循环的任务描述。"),
]
NativeLoopStepsArg = typing.Annotated[
    typing.Optional[list[dict[str, typing.Any]]],
    Field(description="原生编码循环要顺序执行的步骤列表。每项包含 `tool` 和 `args`；执行前会进行预检。"),
]
NativeRepairStepsArg = typing.Annotated[
    list[dict[str, typing.Any]],
    Field(description="模型生成的修复步骤列表；必须包含至少一个 patch 步骤和一个 shell_exec 验证步骤。"),
]
NativeLoopVerifyCommandArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="可选验证命令，以参数数组表达；失败时会返回 verify_diagnostics、自动读取的错误上下文、repair_prompt 和建议读取步骤，覆盖 pytest、tsc、jest/vitest、ruff/mypy、go test、cargo test 常见输出。"),
]
NativeLoopStopOnFailArg = typing.Annotated[
    bool,
    Field(description="步骤失败时是否立即停止后续步骤。"),
]
NativeLoopMaxStepsArg = typing.Annotated[
    int,
    Field(description="本次原生编码循环最多执行的步骤数。"),
]
NativeLoopAutoRepairArg = typing.Annotated[
    typing.Union[bool, str],
    Field(description="验证失败后是否生成 repair_plan：false/off 不启用，true/plan 生成模型可消费的修复计划。模型调用由 Mind 外层 chat/fast/xtra 继续完成。"),
]
NativeLoopAutoRollbackArg = typing.Annotated[
    typing.Union[bool, str],
    Field(description="失败后是否自动按本 run 快照回滚：false/off 不启用，true/always 任意失败回滚，step_failed 仅步骤失败回滚，verify_failed 仅验证失败回滚。"),
]
NativeSessionIdArg = typing.Annotated[
    typing.Optional[str],
    Field(description="原生编码会话 ID；为空时创建新会话，传入已有 ID 时追加新 run 并保留修复轨迹。"),
]
NativeRequiredSessionIdArg = typing.Annotated[
    str,
    Field(description="必填原生编码会话 ID。"),
]
NativeRunIdArg = typing.Annotated[
    typing.Optional[str],
    Field(description="可选 run ID；为空时使用指定 session 的最后一个 run。"),
]
NativePlanActionArg = typing.Annotated[
    str,
    Field(description="计划工具动作：get 或 update。"),
]
NativePlanTodosArg = typing.Annotated[
    typing.Optional[list[dict[str, typing.Any]]],
    Field(description="TODO 列表；每项可包含 id/title/status/details/path。"),
]
NativePlanStringsArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="计划中的字符串列表，例如 assumptions 或 next_steps。"),
]
NativePlanNoteArg = typing.Annotated[
    typing.Optional[str],
    Field(description="追加到计划 notes 的简短备注。"),
]
NativePlanModeArg = typing.Annotated[
    str,
    Field(description="计划更新模式：merge 或 replace。"),
]
NativePlanUpdateArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="native_coding_loop 可选计划更新，支持 todos/assumptions/next_steps/note/mode。"),
]
SandboxExitCodeArg = typing.Annotated[
    int,
    Field(description="云端 sandbox 命令退出码。"),
]
SandboxOutputArg = typing.Annotated[
    str,
    Field(description="云端 sandbox 返回的 stdout 或 stderr 文本。"),
]
SandboxElapsedArg = typing.Annotated[
    typing.Optional[int],
    Field(description="云端 sandbox 执行耗时，单位毫秒。"),
]
SandboxTimedOutArg = typing.Annotated[
    bool,
    Field(description="云端 sandbox 命令是否超时。"),
]
SandboxProviderArg = typing.Annotated[
    str,
    Field(description="云端 sandbox 提供方名称。"),
]
SandboxFileChangesArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="云端 sandbox 观察到的文件变更摘要，可为空。"),
]
SandboxArtifactsArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="云端 sandbox 回传的文件 artifact，可包含 files 列表；每项包含 path、action(create/modify/delete)、content、sha256、bytes。"),
]
SandboxVerifyArg = typing.Annotated[
    bool,
    Field(description="是否将该 sandbox 结果作为验证结果写回 session，并触发 diagnostics/repair_plan。"),
]


if __name__ == '__main__':
    pass
