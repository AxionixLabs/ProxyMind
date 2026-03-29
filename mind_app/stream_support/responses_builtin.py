# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

BUILTIN_NAME_ALIASES: dict[str, str] = {
    "web_search_call"       : "web_search",
    "file_search_call"      : "file_search",
    "code_interpreter_call" : "code_interpreter"
}


def _candidate_payloads(event: dict[str, typing.Any]) -> list[dict[str, typing.Any]]:
    payloads: list[dict[str, typing.Any]] = []

    if isinstance(event, dict):
        payloads.append(event)

        for key in ("arguments", "input", "payload", "data", "meta", "detail"):
            value = event.get(key)
            if isinstance(value, dict):
                payloads.append(value)

    return payloads


def _first_string(payloads: list[dict[str, typing.Any]], *keys: str) -> str:
    for payload in payloads:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def resolve_builtin_name(event: dict[str, typing.Any]) -> str:
    payloads = _candidate_payloads(event)
    for key in ("name", "builtin_type", "builtin_name", "tool_name", "tool"):
        value = _first_string(payloads, key).lower()
        if value:
            return BUILTIN_NAME_ALIASES.get(value, value)
    return "builtin"


def consume_builtin_done(event: dict[str, typing.Any], tracker: typing.Any) -> None:
    """
    消费 builtin done 事件。

    `SegmentTracker` 目前只关心 `sources / source_count` 这类通用元数据，
    因此这里统一下沉给 tracker 自己判断。
    """
    tracker.on_builtin_done(event)


if __name__ == '__main__':
    pass
