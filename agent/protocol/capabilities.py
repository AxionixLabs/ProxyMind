# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field
)
from .json_value import (
    JsonValue,
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)


@dataclass(frozen=True, slots=True)
class McpToolDefinition:
    """描述 MCP 能力向模型暴露的一个工具。"""

    name: str
    description: str = ""
    input_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    meta: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验工具身份并冻结 schema 与元数据。"""
        name = str(self.name or "").strip()
        if not name:
            raise ValueError("MCP tool name is required")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("MCP tool input_schema must be an object")
        if not isinstance(self.meta, Mapping):
            raise TypeError("MCP tool meta must be an object")

        input_schema = freeze_json(
            dict(self.input_schema),
            field_name="MCP tool input_schema",
        )
        meta = freeze_json(dict(self.meta), field_name="MCP tool meta")
        if not isinstance(input_schema, Mapping):
            raise TypeError("MCP tool input_schema must be an object")
        if not isinstance(meta, Mapping):
            raise TypeError("MCP tool meta must be an object")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "input_schema", input_schema)
        object.__setattr__(self, "meta", meta)

    def to_dict(self) -> dict[str, ThawedJsonValue]:
        """返回脱离 SDK 的工具描述快照。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": thaw_object(
                self.input_schema,
                field_name="MCP tool input_schema",
            ),
            "meta": thaw_object(self.meta, field_name="MCP tool meta"),
        }


@dataclass(frozen=True, slots=True)
class McpToolResult:
    """描述 MCP 工具调用完成后的稳定结果。"""

    ok: bool
    text: str = ""
    data: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验结果标志并冻结结构化数据。"""
        if not isinstance(self.ok, bool):
            raise TypeError("MCP tool result ok must be boolean")
        if not isinstance(self.data, Mapping):
            raise TypeError("MCP tool result data must be an object")
        data = freeze_json(
            dict(self.data),
            field_name="MCP tool result data",
        )
        if not isinstance(data, Mapping):
            raise TypeError("MCP tool result data must be an object")
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(self, "data", data)

    def to_dict(self) -> dict[str, ThawedJsonValue]:
        """返回不携带运行时对象的工具结果快照。"""
        return {
            "ok": self.ok,
            "text": self.text,
            "data": thaw_object(
                self.data,
                field_name="MCP tool result data",
            ),
        }


if __name__ == '__main__':
    pass
