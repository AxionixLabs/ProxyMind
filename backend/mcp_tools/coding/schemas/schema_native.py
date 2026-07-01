# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt
)


class ShellCommandItem(BaseModel):
    """单条 shell_command 批量项。"""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(
        description="要执行的 shell 命令字符串；由系统默认 shell 解释执行。"
    )
    cwd: str = Field(
        default=".",
        description="命令工作目录，必须在当前工作区内。"
    )
    timeout_sec: StrictInt = Field(
        default=60,
        ge=1,
        le=600,
        description="命令超时秒数，范围 1-600。"
    )


def shell_command_items_payload(
    items: typing.Iterable[ShellCommandItem | dict[str, typing.Any]] | None
) -> list[dict[str, typing.Any]]:
    """把 shell_command schema 项转换为执行层普通 dict。"""
    payload: list[dict[str, typing.Any]] = []
    for item in items or []:
        if isinstance(item, ShellCommandItem):
            payload.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            payload.append({
                key: item[key]
                for key in ("command", "cwd", "timeout_sec")
                if key in item
            })
    return payload


ShellCommandItemsArg = typing.Annotated[
    list[ShellCommandItem],
    Field(
        min_length=1,
        max_length=12,
        description=(
            "批量 shell 命令列表。每项需包含 command，可包含 cwd 和 timeout_sec；"
            "不要包含 tool 字段；execution 由服务端策略层补充，不需要模型生成。"
        )
    ),
]
WorkspaceForceArg = typing.Annotated[
    bool,
    Field(description="是否跳过 SHA256 基线冲突检查。仅在明确需要覆盖外部改动时使用。"),
]
ApplyPatchArg = typing.Annotated[
    str,
    Field(
        description=(
            "严格 apply_patch 补丁文本；必须以 *** Begin Patch 开始，以 *** End Patch 结束，"
            "并使用 *** Add File / *** Update File / *** Delete File 描述文件变更。"
            "补丁内容行必须带行标记：Add File 的每一行文件内容都必须以 + 开头；"
            "Update File 使用空格表示上下文行、+ 表示新增行、- 表示删除行；"
            "Delete File 只需要文件操作行，不包含文件内容。"
            "重命名或移动文件时使用 *** Update File: old/path 后接 *** Move to: new/path。"
            "不要传裸 JSON、裸 Markdown、统一 diff，或任何未带 +/空格/- 前缀的内容行。"
        )
    ),
]
WorkspaceExpectedSha256MapArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="按文件路径映射的 SHA256 基线；当前文件不匹配时拒绝应用 patch。"),
]
ExecutionMetadataArg = typing.Annotated[
    typing.Optional[dict[str, typing.Any]],
    Field(description="服务端策略层补充的执行裁决，包含 grantId 和 canonicalArguments 等元数据；模型调用时可省略。"),
]


if __name__ == '__main__':
    pass
