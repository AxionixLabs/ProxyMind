# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from protocol.schema.identifiers import (
    normalize_turn_id,
    short_uid,
)
from protocol.schema.environment import normalize_client_environment_snapshot
from protocol.schema.permissions import permission_payload


_AGENT_REQUEST_OPTION_FIELDS = frozenset({
    "agent",
    "extras",
    "metadata",
    "skills",
    "streaming",
    "tool_choice",
})


def empty_primary_request_slot() -> dict[str, str]:
    """返回请求协议要求的空 primary 配置。"""
    return {
        "provider": "",
        "route": "",
        "model": "",
        "apikey": "",
        "base_url": "",
        "reasoning_effort": ""
    }


def normalize_request_slot(
    name: typing.Any,
    slot: dict[str, typing.Any]
) -> dict[str, typing.Any] | None:
    """规范化单个请求模型槽位。"""
    if name != "primary":
        return None

    if slot.get("enabled") is False:
        return empty_primary_request_slot()

    result = {
        key: value
        for key, value in slot.items()
        if key != "enabled"
    }
    result["provider"] = slot.get("kind")
    empty_slot = empty_primary_request_slot()

    empty_slot.update({
        key: str(result.get(key) or "").strip()
        for key in empty_slot
    })

    return empty_slot


def request_llm_conf(pref_config: typing.Any) -> dict[str, typing.Any]:
    """生成请求侧模型配置，移除仅供本地偏好使用的字段。"""
    if not isinstance(pref_config, dict):
        return {"primary": empty_primary_request_slot()}

    result: dict[str, typing.Any] = {}

    for name, slot in pref_config.items():
        if name != "primary":
            continue
        if isinstance(slot, dict):
            normalized_slot = normalize_request_slot(name, slot)
            if normalized_slot is None:
                continue
            result[name] = normalized_slot

    if "primary" not in result:
        result["primary"] = empty_primary_request_slot()
    return result


def request_hosted_tools(pref_config: typing.Any) -> dict[str, typing.Any]:
    """生成请求侧托管工具配置。"""
    config = pref_config if isinstance(pref_config, dict) else {}
    hosted = config.get("hosted_tools") if isinstance(config.get("hosted_tools"), dict) else {}

    groups = hosted.get("groups") if isinstance(hosted.get("groups"), dict) else {}

    enabled_groups = [
        name
        for name in ("perf_engine", "sandbox_cloud")
        if groups.get(name, False) is True
    ]

    return {"enabled_groups": enabled_groups}


async def build_chat_payload(
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict],
    attachments: list[dict[str, typing.Any]] | None = None,
    **kwargs: typing.Any
) -> dict[str, typing.Any]:
    """构建对话请求载荷。"""
    raw_exec_env = kwargs.pop("exec_env", None)
    runtime_exec_env = (
        normalize_client_environment_snapshot(raw_exec_env)
        if raw_exec_env is not None
        else None
    )

    raw_additional_context = kwargs.pop("additional_context", ())
    if not isinstance(raw_additional_context, (tuple, list)):
        raise TypeError("additional context must be a sequence")

    raw_system_message = kwargs.pop("system_message", "")
    if not isinstance(raw_system_message, str):
        raise TypeError("system message must be a string")

    additional_context: list[str] = []

    for value in raw_additional_context:
        if not isinstance(value, str):
            raise TypeError("additional context entries must be strings")
        normalized = value.strip()
        if normalized:
            additional_context.append(normalized)

    turn_id = normalize_turn_id(
        str(kwargs.pop("turn_id", "") or "").strip() or short_uid(12)
    )

    permissions = permission_payload(kwargs.pop("permissions", None))

    unknown_fields = sorted(set(kwargs).difference(_AGENT_REQUEST_OPTION_FIELDS))
    if unknown_fields:
        raise ValueError(
            "unsupported AgentRequest fields: " + ", ".join(unknown_fields)
        )

    payload: dict[str, typing.Any] = {
        "turn_id": turn_id,
        "llm_conf": request_llm_conf(pref_config),
        "message": message,
        "tools": tools,
        "hosted_tools": request_hosted_tools(pref_config),
        **permissions,
        **kwargs,
    }

    if runtime_exec_env is not None:
        payload["exec_env"] = runtime_exec_env

    if attachments:
        payload["attachments"] = attachments
    if additional_context:
        payload["additional_context"] = additional_context

    system_message = raw_system_message.strip()
    if system_message:
        payload["system_message"] = system_message

    return payload


if __name__ == '__main__':
    pass
