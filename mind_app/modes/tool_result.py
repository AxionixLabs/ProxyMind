# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


def tool_result_text(fields: typing.Union[str, dict[str, typing.Any], typing.Any]) -> str:
    """提取工具结果文本，兼容 dict / str / 其他对象。"""
    if isinstance(fields, dict):
        value = fields.get("text")
        return "" if value is None else str(value)
    if fields is None:
        return ""
    return str(fields)


def tool_result_data(fields: typing.Union[str, dict[str, typing.Any], typing.Any]) -> typing.Any:
    """提取工具结果 data 字段，非结构化结果返回 None。"""
    if isinstance(fields, dict):
        return fields.get("data")
    return None


if __name__ == '__main__':
    pass
