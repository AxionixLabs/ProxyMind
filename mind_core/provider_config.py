# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

OPENAI_COMPATIBLE_PROVIDER_NAME = "openai_compatible"
OPENAI_PROVIDER_NAME            = "openai"
ANTHROPIC_PROVIDER_NAME         = "anthropic"

DEFAULT_PROVIDER_NAME = OPENAI_COMPATIBLE_PROVIDER_NAME

SUPPORTED_PROVIDER_OPTIONS = (
    {"value": OPENAI_COMPATIBLE_PROVIDER_NAME, "label": "OpenAI Compatible"},
    {"value": OPENAI_PROVIDER_NAME, "label": "OpenAI"},
    {"value": ANTHROPIC_PROVIDER_NAME, "label": "Anthropic"},
)

DEFAULT_ROUTE_NAME   = "responses"
ANTHROPIC_ROUTE_NAME = "messages"

SUPPORTED_ROUTE_NAMES = {
    DEFAULT_ROUTE_NAME,
    "chat_completions",
    ANTHROPIC_ROUTE_NAME,
}

DEFAULT_REASONING_EFFORT    = "medium"
SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


def default_route_for_provider(provider: object) -> str:
    """返回指定模型服务的默认调用路由。"""
    normalized = str(provider or "").strip().lower()
    if normalized == ANTHROPIC_PROVIDER_NAME:
        return ANTHROPIC_ROUTE_NAME
    return DEFAULT_ROUTE_NAME


if __name__ == "__main__":
    pass
