# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

from agent.application.turns.context import TurnContext
from agent.protocol import ModelStreamRequest

__all__ = (
    "build_model_stream_request",
    "extend_request_context",
)


def extend_request_context(
    options: dict[str, typing.Any],
    *,
    additional_context: typing.Iterable[str] = (),
    system_message: str = "",
) -> None:
    """把 Hook 注入文本合并到模型请求参数。"""
    contexts = [
        text
        for value in additional_context
        for text in [str(value or "").strip()]
        if text
    ]
    if contexts:
        existing = options.get("additional_context")
        merged = list(existing) if isinstance(existing, list) else []
        merged.extend(contexts)
        options["additional_context"] = merged

    system_text = str(system_message or "").strip()
    if system_text:
        options["system_message"] = _join_text(
            str(options.get("system_message") or ""),
            system_text,
        )


def build_model_stream_request(
    context: TurnContext,
    *,
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict[str, typing.Any]],
    options: Mapping[str, typing.Any],
) -> ModelStreamRequest:
    """校验单轮坐标和环境后构建冻结的模型流请求。"""
    request_options = dict(options)
    raw_attachments = request_options.pop("attachments", ())
    attachments = (
        tuple(raw_attachments)
        if isinstance(raw_attachments, (tuple, list))
        else ()
    )
    timeout = request_options.pop("timeout", 60.0)
    environment_snapshot = request_options.pop("exec_env", None)
    if (
        environment_snapshot is not None
        and not isinstance(environment_snapshot, Mapping)
    ):
        raise TypeError("exec_env must be an object")
    request_options["permissions"] = {
        "sandbox_mode": context.permissions.sandbox_mode,
        "approval_policy": context.permissions.approval_policy,
        "approvals_reviewer": context.permissions.approvals_reviewer,
        "network_access": context.permissions.network_access,
    }
    raw_turn_id = request_options.pop("turn_id", context.turn_id)
    if raw_turn_id != context.turn_id:
        raise ValueError("model request turn_id does not match Turn context")
    raw_metadata = request_options.pop("metadata", {})
    if not isinstance(raw_metadata, Mapping):
        raise TypeError("model request metadata must be an object")
    request_metadata = dict(raw_metadata)
    for field_name, expected in (
            ("cid", context.cid),
            ("sid", context.sid),
    ):
        existing = request_metadata.pop(field_name, expected)
        if existing != expected:
            raise ValueError(
                f"model request {field_name} does not match Turn context"
            )
    return ModelStreamRequest(
        cid=context.cid,
        sid=context.sid,
        turn_id=context.turn_id,
        pref_config=pref_config,
        message=message,
        tools=tuple(tools),
        attachments=attachments,
        environment_snapshot=environment_snapshot,
        metadata=request_metadata,
        options=request_options,
        timeout=timeout,
    )


def _join_text(*values: str) -> str:
    """合并非空文本段。"""
    return "\n\n".join(
        text
        for value in values
        for text in [str(value or "").strip()]
        if text
    )


if __name__ == '__main__':
    pass
