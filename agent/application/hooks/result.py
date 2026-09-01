# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.hooks.models import HookVisibleToolResult


def apply_tool_result_effect(
    *,
    ok: bool,
    text: str,
    fields: dict[str, typing.Any],
    replacement_result: typing.Any = None,
    replacement_result_set: bool = False,
    blocked: bool = False,
    feedback_message: str = "",
    additional_context: typing.Iterable[str] = (),
) -> HookVisibleToolResult:
    """把后置 Hook 影响应用到模型可见工具结果。"""
    result_ok = bool(ok)
    result_text = str(text or "")
    result_fields = dict(fields)
    feedback = str(feedback_message or "").strip()

    if blocked:
        result_text = feedback or "PostToolUse hook blocked the tool result"
        result_ok = False
        result_fields = {
            "ok": False,
            "text": result_text,
            "attachments": [],
            "data": {
                "hook_blocked": True,
                "error": result_text,
            },
        }
    elif replacement_result_set:
        result_ok, result_text, result_fields = _coerce_hook_result_fields(
            replacement_result,
            default_ok=result_ok,
        )
    elif feedback:
        result_text = feedback
        result_fields = {
            "ok": result_ok,
            "text": result_text,
            "attachments": [],
            "data": {
                "hook_feedback": True,
            },
        }

    return HookVisibleToolResult(
        ok=result_ok,
        text=result_text,
        fields=result_fields,
        additional_context=tuple(additional_context),
    )


def _coerce_hook_result_fields(
    value: typing.Any,
    *,
    default_ok: bool,
) -> tuple[bool, str, dict[str, typing.Any]]:
    """把 Hook 替换结果转换为稳定工具结果字段。"""
    if isinstance(value, dict):
        raw_fields = dict(value)
        unknown = sorted(
            set(raw_fields).difference({"ok", "text", "attachments", "data"})
        )
        if unknown:
            raise ValueError(
                "hook replacement result contains unknown fields: "
                + ", ".join(unknown)
            )
    else:
        raw_fields = {
            "ok": default_ok,
            "text": _hook_result_text(value),
            "attachments": [],
            "data": {"value": value},
        }

    raw_ok = raw_fields.get("ok", default_ok)
    if not isinstance(raw_ok, bool):
        raise TypeError("hook replacement result ok must be a boolean")
    raw_text = raw_fields.get("text", _hook_result_text(value))
    if not isinstance(raw_text, str):
        raise TypeError("hook replacement result text must be a string")
    attachments = raw_fields.get("attachments", [])
    if not isinstance(attachments, list):
        raise TypeError("hook replacement result attachments must be a list")
    data = raw_fields.get("data", {})
    if not isinstance(data, dict):
        raise TypeError("hook replacement result data must be an object")

    return raw_ok, raw_text, {
        "ok": raw_ok,
        "text": raw_text,
        "attachments": attachments,
        "data": data,
    }


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
