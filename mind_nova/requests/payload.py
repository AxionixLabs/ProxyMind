# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.skills import skills_payload
from .access import (
    DEFAULT_ACCESS_MODE,
    apply_access_mode
)


def resolve_transport_mode(mode: str) -> str:
    """规范化传输模式，保持本地模式与服务端链路一一对应。"""
    return str(mode or "").strip().lower()


def ensure_default_skills(kwargs: dict[str, typing.Any]) -> None:
    """没有声明 skills 或声明为空时，补入本地可用 skills。"""
    skills = kwargs.get("skills")
    if skills is None or (isinstance(skills, (list, tuple)) and not skills):
        kwargs["skills"] = skills_payload()


def empty_primary_request_slot() -> dict[str, str]:
    """返回请求协议要求的空 primary 配置。"""
    return {
        "provider"         : "",
        "route"            : "",
        "model"            : "",
        "apikey"           : "",
        "base_url"         : "",
        "reasoning_effort" : ""
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


async def build_chat_payload(
    mode: str,
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict],
    attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
    **kwargs: typing.Any
) -> dict[str, typing.Any]:
    """构建 chat/fast/xtra 请求载荷。"""
    if not isinstance(runtime_exec_env := kwargs.pop("exec_env", None), dict):
        runtime_exec_env = {}

    ensure_default_skills(kwargs)

    payload = {
        "mode"     : resolve_transport_mode(mode),
        "llm_conf" : request_llm_conf(pref_config),
        "message"  : message,
        "tools"    : tools,
        "exec_env" : runtime_exec_env,
        **kwargs
    }

    apply_access_mode(payload, payload.pop("access_mode", DEFAULT_ACCESS_MODE))

    if attachments:
        payload["attachments"] = attachments

    return payload


if __name__ == '__main__':
    pass
