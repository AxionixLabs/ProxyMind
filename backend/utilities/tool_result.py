# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from dataclasses import (
    dataclass, field
)
from mcp.types import (
    CallToolResult, TextContent
)


@dataclass(slots=True)
class ToolOutput(object):
    """统一描述底层工具执行结果。"""
    ok: bool = True
    text: str = ""
    data: dict[str, typing.Any] = field(default_factory=dict)
    attachments: list[typing.Any] = field(default_factory=list)
    logs: list[typing.Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, typing.Any]:
        """转换为可序列化字典。"""
        return {
            "ok"          : self.ok,
            "text"        : self.text,
            "attachments" : self.attachments,
            "data"        : self.data,
            "logs"        : self.logs
        }


def normalize_tool_output(raw_data: ToolOutput | dict[str, typing.Any]) -> ToolOutput:
    """读取底层工具返回结构。"""
    if isinstance(raw_data, ToolOutput):
        return raw_data

    if isinstance(raw_data, dict):
        return ToolOutput(
            ok=raw_data["ok"],
            text=raw_data["text"],
            attachments=raw_data["attachments"],
            data=raw_data["data"],
            logs=raw_data["logs"]
        )

    return raw_data


def build_tool_result(
    *,
    tool: str,
    args: dict[str, typing.Any] | None,
    raw: typing.Any,
    target: str | None = None
) -> CallToolResult:
    """构造单次工具调用结果。"""
    output = normalize_tool_output(raw)
    data = output.data

    prefix = f"tool={tool}"
    if target is not None:
        prefix += f" target={target}"

    text = output.text or str(data or "")
    result_text = f"{prefix} ok={output.ok} {text}"
    structured: dict[str, typing.Any] | None = {
        "ok"          : output.ok,
        "tool"        : tool,
        "args"        : args or {},
        "text"        : result_text,
        "attachments" : output.attachments,
        "data"        : data
    }
    if target is not None:
        structured["target"] = target

    return CallToolResult(
        content=[TextContent(type="text", text=result_text)],
        structuredContent=structured,
        isError=not output.ok,
        _meta={"logs": output.logs}
    )


if __name__ == '__main__':
    pass
