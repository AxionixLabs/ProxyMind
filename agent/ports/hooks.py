# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable,
    Iterable,
)
from pathlib import Path
from agent.domain.hooks import (
    HookDefinitionConfig,
)

if typing.TYPE_CHECKING:
    from agent.application.hook_catalog import HookCatalogSnapshot
    from agent.application.hook_models import (
        HookDispatchResult,
        HookEventRequest,
        HookRunSummary,
    )
    from agent.domain.hooks import HookStateTable

HookSessionCleanup: typing.TypeAlias = Callable[[str], Awaitable[None]]
HookResourceClose: typing.TypeAlias = Callable[[], Awaitable[None]]


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


class HookStatusPort(typing.Protocol):
    """定义 Hook 执行状态的展示回调端口。"""

    async def started(self, run: "HookRunSummary") -> None:
        """发布一个 Hook 命令已经开始。"""
        ...

    async def completed(self, run: "HookRunSummary") -> None:
        """发布一个 Hook 命令已经完成。"""
        ...


class HookDispatcherPort(typing.Protocol):
    """定义固定执行作用域使用的 Hook 分发端口。"""

    def has_matching(self, event: str, match_value: str = "") -> bool:
        """判断指定事件是否存在匹配 Hook。"""
        ...

    async def dispatch(
        self,
        request: "HookEventRequest",
    ) -> "HookDispatchResult":
        """分发一次生命周期事件。"""
        ...

    def with_default_status_port(
        self,
        status_port: HookStatusPort,
    ) -> "HookDispatcherPort":
        """在尚未绑定展示端时返回带状态端口的分发器。"""
        ...


@typing.runtime_checkable
class HookRegistryPort(typing.Protocol):
    """定义 Hook 发现、构建和资源关闭的组合端口。"""

    def startup_warnings(
        self,
        definitions: Iterable[HookDefinitionConfig],
        *,
        hook_states: "HookStateTable | None" = None,
        warnings: Iterable[str] = (),
    ) -> tuple[str, ...]:
        """返回启动阶段需要报告的 Hook 告警。"""
        ...

    def build(
        self,
        definitions: Iterable[HookDefinitionConfig],
        *,
        hook_states: "HookStateTable | None" = None,
        warnings: Iterable[str] = (),
        status_port: HookStatusPort | None = None,
    ) -> HookDispatcherPort:
        """按当前信任状态构建一个独立 Hook 分发器。"""
        ...

    def inspect(
        self,
        definitions: Iterable[HookDefinitionConfig],
        *,
        hook_states: "HookStateTable | None" = None,
        warnings: Iterable[str] = (),
        workspace: Path,
    ) -> "HookCatalogSnapshot":
        """返回指定工作区的 Hook 管理快照。"""
        ...

    async def cleanup_session(self, session_id: str) -> None:
        """清理指定会话产生的 Hook 临时输出。"""
        ...

    async def close(self) -> None:
        """关闭 Hook 执行器持有的资源。"""
        ...


class HookRegistryFactory(typing.Protocol):
    """定义组合根创建 Hook registry 的工厂。"""

    def __call__(
        self,
        *,
        bypass_hook_trust: bool = False,
    ) -> HookRegistryPort:
        """创建绑定本进程资源的 Hook registry。"""
        ...


if __name__ == '__main__':
    pass
