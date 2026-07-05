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
        "llm_conf" : pref_config,
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
