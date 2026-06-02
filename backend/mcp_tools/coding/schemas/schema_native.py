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
    Field(description="工作区内要复制、移动或重命名的源文件相对路径；不允许越过工作区根目录。"),
]
WorkspaceTargetPathArg = typing.Annotated[
    str,
    Field(description="工作区内复制、移动或重命名后的目标文件相对路径；不允许越过工作区根目录。"),
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
ExecutionMetadataArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="服务端执行裁决；可选，包含 target/state/grantId/canonicalArguments 等字段。"),
]

GitDiffMaxCharsArg = typing.Annotated[
    int,
    Field(description="git diff 最多返回的字符数。"),
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
if __name__ == '__main__':
    pass
