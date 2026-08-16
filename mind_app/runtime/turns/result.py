# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from dataclasses import (
    dataclass,
    field
)

RunStatus = typing.Literal[
    "completed",
    "failed",
    "incomplete",
    "interrupted",
    "reconciliation_required",
]


@dataclass(frozen=True, slots=True)
class RunResult(object):
    """描述一次模型运行的结构化结果。"""
    status: RunStatus
    assistant_text: str = ""
    usage: dict[str, typing.Any] = field(default_factory=dict)
    error: str | None = None
    additional_context: tuple[str, ...] = ()
    response_id: str = ""
    model: str = ""
    route: str = ""
    request_id: str = ""
    service_tier: str = ""
    stop_reason: str | None = None
    stop_sequence: str | None = None
    reason: str = ""
    can_continue: bool = False

    def __post_init__(self) -> None:
        """规范化轮次结果中的可变数据和附加上下文。"""
        object.__setattr__(self, "usage", copy.deepcopy(dict(self.usage or {})))
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )

    @property
    def ok(self) -> bool:
        """返回运行是否完整成功。"""
        return self.status == "completed"

    @property
    def exit_code(self) -> int:
        """返回命令行使用的进程退出码。"""
        return 0 if self.ok else 1

    def to_dict(self) -> dict[str, typing.Any]:
        """返回可用于协议输出的结构化结果。"""
        result: dict[str, typing.Any] = {
            "status": self.status,
            "assistant_text": self.assistant_text,
            "usage": copy.deepcopy(self.usage),
            "error": self.error,
            "exit_code": self.exit_code
        }

        for field_name in (
            "response_id",
            "model",
            "route",
            "request_id",
            "service_tier",
            "stop_reason",
            "stop_sequence",
            "reason",
        ):
            value = getattr(self, field_name)
            if value not in {None, ""}:
                result[field_name] = value

        if self.status == "incomplete":
            result["can_continue"] = self.can_continue
        if self.additional_context:
            result["additional_context"] = list(self.additional_context)

        return result


if __name__ == '__main__':
    pass
