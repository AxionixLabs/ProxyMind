# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

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
]


@dataclass(frozen=True, slots=True)
class RunResult(object):
    """描述一次模型运行的结构化结果。"""
    status: RunStatus
    assistant_text: str = ""
    usage: dict[str, typing.Any] = field(default_factory=dict)
    error: str | None = None
    additional_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """规范化失败轮次需要保留的附加上下文。"""
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
        result = {
            "status"         : self.status,
            "assistant_text" : self.assistant_text,
            "usage"          : dict(self.usage),
            "error"          : self.error,
            "exit_code"      : self.exit_code
        }
        if self.additional_context:
            result["additional_context"] = list(self.additional_context)
        return result


if __name__ == '__main__':
    pass
