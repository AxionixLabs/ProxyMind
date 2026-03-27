# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


def _short_status_text(value: typing.Any, limit: int = 48) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _humanize_status_text(value: typing.Any, limit: int = 48) -> str:
    text = _short_status_text(value, limit=limit).replace("_", " ").strip()
    return " ".join(text.split())


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


def _first_value(payloads: list[dict[str, typing.Any]], *keys: str) -> typing.Any:
    for payload in payloads:
        for key in keys:
            if key in payload:
                return payload.get(key)
    return None


def _extract_query_text(raw: typing.Any) -> typing.Optional[str]:
    if isinstance(raw, str):
        text = _short_status_text(raw)
        return text or None

    if isinstance(raw, dict):
        for key in (
            "query",
            "q",
            "text",
            "term",
            "keyword",
            "value",
            "title",
            "name",
            "display_text",
        ):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return _short_status_text(value)

    return None


def _extract_query_list(raw: typing.Any) -> list[str]:
    if isinstance(raw, list):
        return [text for text in (_extract_query_text(item) for item in raw) if text]

    if isinstance(raw, dict):
        for key in ("queries", "items", "values", "list"):
            if key in raw:
                return _extract_query_list(raw.get(key))
        text = _extract_query_text(raw)
        return [text] if text else []

    text = _extract_query_text(raw)
    return [text] if text else []


def build_builtin_search_status(event: dict[str, typing.Any]) -> str:
    payloads = _candidate_payloads(event)
    action   = _humanize_status_text(_first_string(payloads, "action", "phase"))
    queries  = _first_value(payloads, "queries", "query", "q", "search_query", "search_queries")

    if queries is not None:
        picked = _extract_query_list(queries)[:1]
        if picked:
            if action:
                return f"{action}: {picked[0]}"
            return f"searching {picked[0]}"

    if action:
        return action

    return "searching"

if __name__ == '__main__':
    pass
