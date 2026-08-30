# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ==== 

import typing
from agent.domain.hooks import (
    HookDefinitionConfig,
)


class HookCommandResult(typing.Protocol):
    """定义命令 Hook 执行器返回的已校验结果字段。"""

    data: dict[str, typing.Any]
    stderr: str
    business_block: bool
    block_reason: str


class HookCommandRunner(typing.Protocol):
    """定义 Hook runtime 调用本地命令执行器的端口。"""

    async def execute(
        self,
        definition: HookDefinitionConfig,
        payload: dict[str, typing.Any],
    ) -> HookCommandResult:
        """执行命令并返回已解析的 Hook 输出。"""
        ...


class HookContextSpiller(typing.Protocol):
    """定义 Hook 输出超限时写入会话临时文件的端口。"""

    async def spill_context(
        self,
        text: str,
        *,
        session_id: str,
        channel: str = "additional-context",
        preview_chars: int | None = None,
    ) -> str:
        """写入完整上下文并返回可交给模型的恢复摘要。"""
        ...


if __name__ == '__main__':
    pass
