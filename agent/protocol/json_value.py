# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from collections.abc import Mapping
from types import MappingProxyType

JsonValue: typing.TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)

ThawedJsonValue: typing.TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | list["ThawedJsonValue"]
    | dict[str, "ThawedJsonValue"]
)


def freeze_json(value: typing.Any, *, field_name: str) -> JsonValue:
    """校验并冻结一项 JSON 兼容值。"""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            freeze_json(item, field_name=field_name)
            for item in value
        )
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError(f"{field_name} contains a non-string key")
        return MappingProxyType({
            key: freeze_json(item, field_name=field_name)
            for key, item in value.items()
        })
    raise TypeError(f"{field_name} contains a non-serializable value")


def thaw_json(value: JsonValue) -> ThawedJsonValue:
    """把冻结的 JSON 值复制为普通容器。"""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def thaw_object(value: JsonValue, *, field_name: str) -> dict[str, ThawedJsonValue]:
    """解冻并确认顶层值是 JSON 对象。"""
    thawed = thaw_json(value)
    if not isinstance(thawed, dict):
        raise TypeError(f"{field_name} must be an object")
    return thawed


if __name__ == '__main__':
    pass
