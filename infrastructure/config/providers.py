# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re

OPENAI_COMPATIBLE_PROVIDER_KIND = "openai_compatible"
OPENAI_PROVIDER_KIND = "openai"
ANTHROPIC_PROVIDER_KIND = "anthropic"

DEFAULT_PROVIDER_ID = "openai-main"
DEFAULT_PROVIDER_KIND = OPENAI_PROVIDER_KIND

SUPPORTED_PROVIDER_OPTIONS = (
    {"value": OPENAI_PROVIDER_KIND, "label": "OpenAI"},
    {"value": OPENAI_COMPATIBLE_PROVIDER_KIND, "label": "OpenAI Compatible"},
    {"value": ANTHROPIC_PROVIDER_KIND, "label": "Anthropic"},
)

SUPPORTED_PROVIDER_KINDS = frozenset(
    option["value"] for option in SUPPORTED_PROVIDER_OPTIONS
)

PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

DEFAULT_ROUTE_NAME = "responses"
ANTHROPIC_ROUTE_NAME = "messages"

SUPPORTED_ROUTE_NAMES = {
    DEFAULT_ROUTE_NAME,
    "chat_completions",
    ANTHROPIC_ROUTE_NAME,
}

DEFAULT_REASONING_EFFORT = "medium"
SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


def is_valid_provider_id(value: object) -> bool:
    """判断 Provider Profile 标识是否可用于配置路径。"""
    normalized = str(value or "").strip()
    return bool(normalized and PROVIDER_ID_PATTERN.fullmatch(normalized))


def default_route_for_kind(kind: object) -> str:
    """返回指定模型协议的默认调用路由。"""
    normalized = str(kind or "").strip().lower()
    if normalized == ANTHROPIC_PROVIDER_KIND:
        return ANTHROPIC_ROUTE_NAME
    return DEFAULT_ROUTE_NAME


def supported_routes_for_kind(kind: object) -> tuple[str, ...]:
    """返回指定模型协议允许使用的调用路由。"""
    normalized = str(kind or "").strip().lower()
    if normalized == ANTHROPIC_PROVIDER_KIND:
        return ANTHROPIC_ROUTE_NAME,
    return DEFAULT_ROUTE_NAME, "chat_completions"


if __name__ == "__main__":
    pass
