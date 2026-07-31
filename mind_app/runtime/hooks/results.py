# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from .models import ToolCallRunResult


@dataclass(frozen=True, slots=True)
class HookVisibleToolResult:
    """描述应用后置 Hook 后模型可见的工具结果。"""
    ok: bool
    text: str
    fields: dict[str, typing.Any] = field(default_factory=dict)
    additional_context: tuple[str, ...] = ()
    system_message: str = ""

    def __post_init__(self) -> None:
        """复制可变字段并规范化反馈文本。"""
        object.__setattr__(self, "fields", dict(self.fields))
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )
        object.__setattr__(
            self,
            "system_message",
            str(self.system_message or "").strip(),
        )


def apply_tool_result_effect(
    *,
    ok: bool,
    text: str,
    fields: dict[str, typing.Any],
    hook_run: ToolCallRunResult[typing.Any]
) -> HookVisibleToolResult:
    """把后置 Hook 影响应用到模型可见工具结果。"""
    result_ok     = bool(ok)
    result_text   = str(text or "")
    result_fields = dict(fields)

    if hook_run.replacement_result_set:
        result_ok, result_text, result_fields = _coerce_hook_result_fields(
            hook_run.replacement_result,
            default_ok=result_ok,
        )
    elif hook_run.suppress_original_output:
        result_text = str(hook_run.reason or "tool result suppressed by hook")
        result_ok   = False

        result_fields = {
            "ok": False,
            "text": result_text,
            "data": {
                "hook_suppressed": True,
                "error": result_text,
            },
        }

    return HookVisibleToolResult(
        ok=result_ok,
        text=result_text,
        fields=result_fields,
        additional_context=hook_run.additional_context,
        system_message=hook_run.system_message,
    )


def _coerce_hook_result_fields(
    value: typing.Any,
    *,
    default_ok: bool
) -> tuple[bool, str, dict[str, typing.Any]]:
    """把 Hook 替换结果转换为稳定工具结果字段。"""
    if isinstance(value, dict):
        fields = dict(value)
    else:
        fields = {
            "ok": default_ok,
            "text": _hook_result_text(value),
            "data": value,
        }

    ok = bool(fields["ok"]) if isinstance(fields.get("ok"), bool) else default_ok

    fields["ok"] = ok

    text = str(
        fields.get("text")
        or fields.get("error")
        or _hook_result_text(value)
    )
    fields["text"] = text

    return ok, text, fields


def _hook_result_text(value: typing.Any) -> str:
    """返回 Hook 替换结果的简短文本表示。"""
    if isinstance(value, str):
        return value
    try:
        return str(value if value is not None else "")
    except (TypeError, ValueError):
        return ""


if __name__ == '__main__':
    pass
