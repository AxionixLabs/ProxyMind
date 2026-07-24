# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import re
import typing
from loguru import logger

_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/@+\\-]+$")
_MAX_FIELD_LENGTH = 320


def _format_field(value: typing.Any) -> str:
    """把日志字段转换为稳定的单行文本。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)

    structured = isinstance(value, (dict, list, tuple))
    if structured:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    else:
        text = str(value)

    text = " ".join(text.split())
    if len(text) > _MAX_FIELD_LENGTH:
        text = f"{text[:_MAX_FIELD_LENGTH - 3]}..."
    if structured:
        return text
    if text and _SAFE_VALUE.fullmatch(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def observe(event: str, *, level: str = "DEBUG", **fields: typing.Any) -> None:
    """写入一条可检索的结构化行日志。"""
    parts = [f"event={_format_field(event)}"]
    parts.extend(
        f"{key}={_format_field(value)}"
        for key, value in fields.items()
        if value is not None
    )
    logger.log(level.upper(), "{}", " | ".join(parts))


def observe_exception(
    event: str,
    error: BaseException,
    *,
    level: str = "ERROR",
    **fields: typing.Any,
) -> None:
    """写入带异常类型和摘要的结构化行日志。"""
    observe(
        event,
        level=level,
        **fields,
        error_type=type(error).__name__,
        error=str(error),
    )
