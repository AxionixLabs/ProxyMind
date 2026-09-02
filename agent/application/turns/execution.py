# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
    replace,
)
from types import MappingProxyType

from agent.ports import HookExecutionScopePort
from protocol.schema.identifiers import short_uid
from .context import TurnContext

__all__ = ("TurnExecution", "create_continuation_execution")


@dataclass(frozen=True, slots=True)
class TurnExecution:
    """保存已经固定身份、会话和 Hook 作用域的模型执行。"""

    context: TurnContext
    message: str
    hook_scope: HookExecutionScopePort
    metadata: typing.Mapping[str, typing.Any] = field(default_factory=dict)
    additional_context: tuple[str, ...] = ()
    system_message: str = ""
    input_payload: typing.Mapping[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """固定执行元数据并校验会话标识一致。"""
        if not isinstance(self.context, TurnContext):
            raise TypeError("turn context is required")
        if not isinstance(self.message, str):
            raise TypeError("turn message must be a string")
        if not isinstance(self.hook_scope, HookExecutionScopePort):
            raise TypeError("turn hook scope is required")
        if not isinstance(self.additional_context, (tuple, list)):
            raise TypeError("turn additional context must be a sequence")
        if not isinstance(self.system_message, str):
            raise TypeError("turn system message must be a string")
        if not isinstance(self.input_payload, Mapping):
            raise TypeError("turn input payload must be a mapping")

        additional_context: list[str] = []
        for value in self.additional_context:
            if not isinstance(value, str):
                raise TypeError("turn additional context entries must be strings")
            normalized = value.strip()
            if normalized:
                additional_context.append(normalized)

        self.hook_scope.require_turn(self.context)

        metadata = dict(self.metadata)

        expected = {
            "cid": self.context.cid,
            "sid": self.context.sid,
        }

        for key, value in expected.items():
            provided = str(metadata.get(key) or "").strip()
            if provided and provided != value:
                raise ValueError(f"turn metadata {key} does not match context")
            metadata[key] = value

        object.__setattr__(self, "metadata", MappingProxyType(metadata))
        object.__setattr__(self, "additional_context", tuple(additional_context))
        object.__setattr__(self, "system_message", self.system_message.strip())
        object.__setattr__(
            self,
            "input_payload",
            MappingProxyType({
                **dict(self.input_payload),
                "content": self.message,
            }),
        )


def create_continuation_execution(
    execution: TurnExecution,
    message: str,
    *,
    continuation_count: int | None = None,
    additional_context: typing.Iterable[str] = (),
    system_message: str = "",
) -> TurnExecution:
    """在当前会话中创建一次续跑模型执行。"""
    if continuation_count is None:
        raw_count = execution.metadata.get("continuation_count")
        try:
            next_count = max(0, int(raw_count or 0)) + 1
        except (TypeError, ValueError):
            next_count = 1
    else:
        try:
            next_count = max(0, int(continuation_count))
        except (TypeError, ValueError):
            next_count = 0

    context = replace(
        execution.context,
        turn_id=short_uid(12),
        session_started=False,
        session_start_reason="",
    )
    metadata = dict(execution.metadata)
    metadata.update({
        "continuation_of_turn_id": (
            metadata.get("continuation_of_turn_id")
            or execution.context.turn_id
        ),
        "continuation_count": next_count,
    })

    return TurnExecution(
        context=context,
        message=message,
        hook_scope=execution.hook_scope.for_turn(context),
        metadata=metadata,
        additional_context=tuple(additional_context),
        system_message=system_message,
    )


if __name__ == '__main__':
    pass
