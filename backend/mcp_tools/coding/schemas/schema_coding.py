# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

CodingPromptArg = typing.Annotated[
    str,
    Field(description="要交给 codex CLI 的原始提示词。"),
]
CodingWorkDirArg = typing.Annotated[
    typing.Optional[str],
    Field(description="执行目录；为空时使用当前 Helix 工作目录。"),
]
CodingProfileArg = typing.Annotated[
    typing.Optional[str],
    Field(description="CLI profile 名称；为空时沿用默认配置。"),
]
CodingModelArg = typing.Annotated[
    typing.Optional[str],
    Field(description="目标模型名；为空时沿用 codex 默认模型。"),
]
CodingSandboxArg = typing.Annotated[
    typing.Optional[typing.Literal["read-only", "workspace-write", "danger-full-access"]],
    Field(description="Codex CLI 沙箱模式。"),
]
CodingFullAutoArg = typing.Annotated[
    bool,
    Field(description="是否追加 `--full-auto`；默认开启。"),
]
CodingSkipGitRepoCheckArg = typing.Annotated[
    bool,
    Field(description="是否跳过 git 仓库检查；默认开启。"),
]
CodingEphemeralArg = typing.Annotated[
    bool,
    Field(description="是否启用 `--ephemeral`；默认关闭。"),
]
CodingJsonOutputArg = typing.Annotated[
    bool,
    Field(description="是否启用 `--json` 事件流输出；默认关闭。"),
]
CodingTimeoutSecArg = typing.Annotated[
    typing.Optional[int],
    Field(description="执行超时秒数；为空时不主动超时。"),
]
CodingExtraArgsArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="附加 CLI 参数；每项为一个独立 token。"),
]


if __name__ == '__main__':
    pass
