# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

RunMode = typing.Literal["chat", "fast", "plan", "xtra"]
MODES: tuple[RunMode, ...] = typing.get_args(RunMode)
RUN_MODE_SET: frozenset[RunMode] = frozenset(MODES)


if __name__ == '__main__':
    pass
