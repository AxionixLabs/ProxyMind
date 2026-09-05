# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.application.turns.context import TurnContext
from agent.ports import TurnInputEventHandler
from agent.protocol import ModelStreamEndReason


@dataclass(frozen=True, slots=True)
class TurnObservationCallbacks:
    """保存既有远端 Turn 观察期间由前端提供的生命周期回调。"""

    input_context: typing.Callable[[TurnContext], None] | None = None
    input_event: TurnInputEventHandler | None = None
    stream_end: typing.Callable[[ModelStreamEndReason], None] | None = None
    interrupted: typing.Callable[[], None] | None = None


if __name__ == '__main__':
    pass
