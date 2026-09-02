# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agent.ports.javascript import (
    JavaScriptExecutionError,
    JavaScriptFailureKind,
)

KERNEL_RELATIVE_PATH = Path("kernel.js")
PARSER_RELATIVE_PATH = Path("vendor") / "meriyah.umd.min.js"
KERNEL_SHA256 = "70dc77d3172ce04fc34b56b0885936a0b7e522bad94396be92ba319d9df96ae8"
PARSER_SHA256 = "446def5e8718bb25a18d9bb0bdc1a8b40f4ddde8c071a08c970638b6bdfc4562"


@dataclass(frozen=True, slots=True)
class JavaScriptBundle:
    """描述随客户端原子发布的不可变 JavaScript 运行时资产。"""

    root: Path

    @classmethod
    def at(cls, root: str | Path) -> "JavaScriptBundle":
        """从显式资产根创建不可变 bundle 描述。"""
        return cls(root=Path(root).expanduser().resolve())

    @property
    def kernel_path(self) -> Path:
        """返回不可变 Kernel 入口。"""
        return self.root / KERNEL_RELATIVE_PATH

    @property
    def parser_path(self) -> Path:
        """返回 Kernel 使用的固定解析器资产。"""
        return self.root / PARSER_RELATIVE_PATH

    def verify(self) -> None:
        """验证文件集合和内容散列，不修改资产。"""
        expected = {
            KERNEL_RELATIVE_PATH: KERNEL_SHA256,
            PARSER_RELATIVE_PATH: PARSER_SHA256,
        }
        for relative_path, expected_sha256 in expected.items():
            path = self.root / relative_path
            if not path.is_file():
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.UNAVAILABLE,
                    f"JavaScript sidecar asset is missing: {relative_path.as_posix()}",
                )
            try:
                content = path.read_bytes()
            except OSError as error:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.UNAVAILABLE,
                    "JavaScript sidecar asset cannot be read: "
                    f"{relative_path.as_posix()}",
                ) from error
            actual_sha256 = hashlib.sha256(content).hexdigest()
            if actual_sha256 != expected_sha256:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.UNAVAILABLE,
                    "JavaScript sidecar asset integrity check failed: "
                    f"{relative_path.as_posix()}",
                )


if __name__ == "__main__":
    pass
