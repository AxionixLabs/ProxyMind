# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


WorkspacePathArg = typing.Annotated[
    str,
    Field(description="工作区内相对路径；不允许越过工作区根目录。"),
]
WorkspaceContentArg = typing.Annotated[
    str,
    Field(description="要写入文件的完整文本内容；用于创建或整体覆盖文件。"),
]
ShellCallItemsArg = typing.Annotated[
    list[dict[str, typing.Any]],
    Field(
        description=(
            "shell calls 的步骤列表。每项包含 tool 和 args；"
            "仅允许 shell_command。每项 args 需包含 command、cwd、timeout_sec 和 execution。"
        )
    ),
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
    Field(description="严格 unified diff 原文；必须包含 ---/+++ 文件头，禁止 UI 行号、Markdown 和解释文字。"),
]
WorkspaceExpectedSha256MapArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="按文件路径映射的 SHA256 基线；当前文件不匹配时拒绝应用 patch。"),
]

ShellCommandArg = typing.Annotated[
    str,
    Field(description="要执行的 shell 命令字符串；由系统默认 shell 解释执行。"),
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
    Field(description="服务端执行裁决；shell_command 必须包含 grantId 和 canonicalArguments 等执行元数据。"),
]

if __name__ == '__main__':
    pass
