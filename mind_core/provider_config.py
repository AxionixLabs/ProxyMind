# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

OPENAI_COMPATIBLE_PROVIDER_NAME = "openai_compatible"
OPENAI_PROVIDER_NAME            = "openai"

DEFAULT_PROVIDER_NAME = OPENAI_COMPATIBLE_PROVIDER_NAME

SUPPORTED_PROVIDER_OPTIONS = (
    {"value": OPENAI_COMPATIBLE_PROVIDER_NAME, "label": "OpenAI Compatible"},
    {"value": OPENAI_PROVIDER_NAME, "label": "OpenAI"},
)

DEFAULT_ROUTE_NAME    = "responses"
SUPPORTED_ROUTE_NAMES = {"responses", "chat_completions"}

DEFAULT_REASONING_EFFORT    = "medium"
SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


if __name__ == "__main__":
    pass
