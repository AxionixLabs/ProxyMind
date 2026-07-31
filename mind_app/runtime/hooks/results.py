# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import HookVisibleToolResult


def apply_tool_result_effect(
    *,
    ok: bool,
    text: str,
    fields: dict[str, typing.Any],
    replacement_result: typing.Any = None,
    replacement_result_set: bool = False,
    suppress_original_output: bool = False,
    reason: str = "",
    additional_context: typing.Iterable[str] = (),
    system_message: str = ""
) -> HookVisibleToolResult:
    """把后置 Hook 影响应用到模型可见工具结果。"""
    result_ok     = bool(ok)
    result_text   = str(text or "")
    result_fields = dict(fields)

    if replacement_result_set:
        result_ok, result_text, result_fields = _coerce_hook_result_fields(
            replacement_result,
            default_ok=result_ok,
        )
    elif suppress_original_output:
        result_text = str(reason or "tool result suppressed by hook")
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
        additional_context=tuple(additional_context),
        system_message=system_message,
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
