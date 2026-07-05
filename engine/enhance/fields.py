# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from mcp.types import CallToolResult


def fields(result: CallToolResult) -> typing.Union[dict[str, typing.Any], str]:
    """提取工具返回的结构化字段或首段文本。"""
    return sc if (sc := result.structuredContent) else result.content[0].text


def fields_map(result: CallToolResult) -> dict[str, typing.Any]:
    """把工具返回归一为包含 text、attachments、data 的字典。"""
    result_fields = fields(result)
    if isinstance(result_fields, dict):
        return result_fields

    if isinstance(result_fields, str):
        raw = result_fields.strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed, dict):
                    return parsed

        return {
            "ok"          : True,
            "text"        : result_fields,
            "attachments" : [],
            "data"        : {}
        }

    return {
        "ok"          : True,
        "text"        : str(result_fields),
        "attachments" : [],
        "data"        : {}
    }


def normalize_element(element: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """归一化单个 agent 结果并保留本地附件描述。"""
    attachments = element.get("attachments", [])
    if not isinstance(attachments, list):
        attachments = []

    normalized: list[dict[str, typing.Any]] = []
    for item in attachments:
        if isinstance(item, dict):
            normalized.append(item)
        elif hasattr(item, "to_dict"):
            normalized.append(item.to_dict())

    ok_raw = element.get("ok")
    ok = ok_raw if isinstance(ok_raw, bool) else False

    return {
        "ok"          : ok,
        "text"        : str(element.get("text") or ""),
        "attachments" : normalized
    }


def tool_data(result_fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取工具结果的结构化 data。"""
    data = result_fields.get("data")
    return data if isinstance(data, dict) else {}


def tool_payload(result_fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """提取单次工具结果中的业务载荷。"""
    return tool_data(result_fields)


def tool_target(result_fields: dict[str, typing.Any], payload: dict[str, typing.Any]) -> str:
    """提取单次工具结果对应的目标标识。"""
    target = result_fields.get("target") or payload.get("serial") or result_fields.get("tool")
    return str(target or "default")


if __name__ == '__main__':
    pass
