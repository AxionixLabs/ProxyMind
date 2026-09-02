# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.hooks.models import HookVisibleToolResult


def apply_tool_result_effect(
    *,
    ok: bool,
    text: str,
    fields: dict[str, typing.Any],
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


if __name__ == '__main__':
    pass
