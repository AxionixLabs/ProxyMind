# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from types import MappingProxyType

from agent.protocol import RunEvent

_TERMINAL_EVENT_STATUS: dict[str, str] = {
    "run_completed": "completed",
    "run_failed": "failed",
    "run_incomplete": "incomplete",
    "run_interrupted": "interrupted",
    "run_cancelled": "cancelled",
    "run_reconciliation_required": "reconciliation_required",
}


@dataclass(frozen=True, slots=True)
class RunResultProjection:
    """保存从本地终态事件读取的稳定结果投影。"""

    status: str
    exit_code: int
    result: Mapping[str, typing.Any]

    def __post_init__(self) -> None:
        """复制结果字典并校验退出码类型。"""
        if not self.status:
            raise ValueError("projection status is required")
        if isinstance(self.exit_code, bool) or not isinstance(
            self.exit_code,
            int,
        ):
            raise TypeError("projection exit_code must be an integer")
        object.__setattr__(
            self,
            "result",
            MappingProxyType(dict(self.result)),
        )


def project_run_result(events: Sequence[RunEvent]) -> RunResultProjection:
    """从一个 Run 的连续事件中读取唯一终态结果。"""
    if not events:
        raise ValueError("run events are required")

    run_ids = {event.run_id for event in events}
    session_ids = {event.session_id for event in events}
    sequences = [event.sequence for event in events]
    if len(run_ids) != 1 or len(session_ids) != 1:
        raise ValueError("run events must share one identity")
    if sequences != list(range(1, len(events) + 1)):
        raise ValueError("run event sequence is not contiguous")

    terminal_events: list[RunEvent] = []
    for event in events:
        if event.kind == "run_redispatch_queued":
            if (
                len(terminal_events) != 1
                or terminal_events[0].kind != "run_reconciliation_required"
            ):
                raise ValueError(
                    "run redispatch must supersede one reconciliation event"
                )
            terminal_events.clear()
            continue
        if event.kind in _TERMINAL_EVENT_STATUS:
            terminal_events.append(event)
    if len(terminal_events) != 1 or terminal_events[0] is not events[-1]:
        raise ValueError("run events require one final terminal event")

    terminal = terminal_events[0]
    expected_status = _TERMINAL_EVENT_STATUS[terminal.kind]
    payload = terminal.to_dict()["payload"]
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TypeError("terminal event result must be an object")

    status = str(result.get("status") or "").strip()
    exit_code = result.get("exit_code")
    if status != expected_status or payload.get("status") != status:
        raise ValueError("terminal event result status is inconsistent")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise TypeError("terminal event exit_code must be an integer")
    expected_exit_code = 0 if status == "completed" else 1
    if exit_code != expected_exit_code:
        raise ValueError("terminal event exit_code is inconsistent")

    return RunResultProjection(
        status=status,
        exit_code=exit_code,
        result=result,
    )


if __name__ == '__main__':
    pass
