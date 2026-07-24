# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


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
