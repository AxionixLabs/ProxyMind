# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import enum
import typing
from collections.abc import (
    Iterable,
    Mapping,
)
from dataclasses import (
    dataclass,
    field,
)

from agent.protocol.json_value import (
    JsonValue,
    freeze_json,
)


class LocalToolSource(enum.StrEnum):
    """标识本地工具结果的执行来源。"""

    CLIENT = "client"
    BUILTIN = "builtin"


def _freeze_object(
    value: Mapping[str, typing.Any],
    *,
    field_name: str,
) -> Mapping[str, JsonValue]:
    """校验并冻结工具结果中的 JSON 对象。"""
    frozen = freeze_json(dict(value), field_name=field_name)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return frozen


def _freeze_items(
    values: Iterable[typing.Any],
    *,
    field_name: str,
) -> tuple[JsonValue, ...]:
    """校验并冻结工具结果中的 JSON 数组。"""
    frozen = freeze_json(list(values), field_name=field_name)
    if not isinstance(frozen, tuple):
        raise TypeError(f"{field_name} must be an array")
    return frozen


@dataclass(frozen=True, slots=True)
class LocalToolResult:
    """保存本地工具的稳定结果，不暴露 MCP SDK 对象。"""

    tool: str
    source: LocalToolSource
    ok: bool
    text: str
    args: Mapping[str, JsonValue] = field(default_factory=dict)
    data: Mapping[str, JsonValue] = field(default_factory=dict)
    attachments: tuple[JsonValue, ...] = ()
    logs: tuple[JsonValue, ...] = ()

    def __post_init__(self) -> None:
        """校验身份并冻结全部结构化字段。"""
        name = self.tool.strip()
        if not name:
            raise ValueError("local tool result name is required")
        if not isinstance(self.source, LocalToolSource):
            raise TypeError("local tool result source is required")
        if not isinstance(self.ok, bool):
            raise TypeError("local tool result status must be a boolean")
        if not isinstance(self.text, str):
            raise TypeError("local tool result text must be a string")

        object.__setattr__(self, "tool", name)
        object.__setattr__(
            self,
            "args",
            _freeze_object(self.args, field_name="local tool result args"),
        )
        object.__setattr__(
            self,
            "data",
            _freeze_object(self.data, field_name="local tool result data"),
        )
        object.__setattr__(
            self,
            "attachments",
            _freeze_items(
                self.attachments,
                field_name="local tool result attachments",
            ),
        )
        object.__setattr__(
            self,
            "logs",
            _freeze_items(self.logs, field_name="local tool result logs"),
        )


def client_tool_result(
    *,
    tool: str,
    ok: bool,
    text: str,
    data: Mapping[str, typing.Any] | None = None,
    args: Mapping[str, typing.Any] | None = None,
    attachments: Iterable[typing.Any] | None = None,
    logs: Iterable[typing.Any] | None = None,
) -> LocalToolResult:
    """构造带标准客户端摘要的本地工具结果。"""
    result_text = f"tool={tool} source=client ok={ok} {text}".strip()
    return LocalToolResult(
        tool=tool,
        source=LocalToolSource.CLIENT,
        ok=ok,
        text=result_text,
        args=dict(args or {}),
        data=dict(data or {}),
        attachments=tuple(attachments or ()),
        logs=tuple(logs or ()),
    )


__all__ = (
    "LocalToolResult",
    "LocalToolSource",
    "client_tool_result",
)
