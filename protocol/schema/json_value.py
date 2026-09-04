# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

JsonValue: typing.TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)
JsonObject: typing.TypeAlias = dict[str, JsonValue]


if __name__ == '__main__':
    pass
