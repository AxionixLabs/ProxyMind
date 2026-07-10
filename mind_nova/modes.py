# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

RunMode = typing.Literal["chat", "fast", "xtra"]

MODES: tuple[RunMode, ...] = typing.get_args(RunMode)

RUN_MODE_SET: frozenset[RunMode] = frozenset(MODES)

DEFAULT_RUN_MODE: RunMode = "xtra"


if __name__ == '__main__':
    pass
