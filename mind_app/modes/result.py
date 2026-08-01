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
        return {
            "status"         : self.status,
            "assistant_text" : self.assistant_text,
            "usage"          : dict(self.usage),
            "error"          : self.error,
            "exit_code"      : self.exit_code
        }


if __name__ == '__main__':
    pass
