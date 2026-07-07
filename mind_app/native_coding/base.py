# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
import hashlib
from pathlib import Path
from mind_nova import const
from mind_app.native_coding.encoding import (
    decode_process_output,
    process_output_encodings
)


class NativeCodingBase(object):
    """原生编码工具的共享状态与基础辅助方法。"""

    agent_id: str = "native_coding"

    DEFAULT_EXCLUDES = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "node_modules",
        "dist",
        "build",
        ".idea",
        ".vscode"
    }

    TEXT_SUFFIXES = {
        ".py",
        ".pyi",
        ".md",
        ".txt",
        ".c",
        ".cc",
        ".cjs",
        ".clj",
        ".cls",
        ".cpp",
        ".cs",
        ".dart",
        ".ex",
        ".exs",
        ".fs",
        ".fsi",
        ".fsx",
        ".h",
        ".hh",
        ".hpp",
        ".java",
        ".json",
        ".kt",
        ".kts",
        ".lua",
        ".mjs",
        ".mm",
        ".php",
        ".pl",
        ".pm",
        ".proto",
        ".rb",
        ".scala",
        ".scss",
        ".svelte",
        ".swift",
        ".toml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".sh",
        ".bat",
        ".ps1",
        ".sql",
        ".xml",
        ".svg",
        ".csv",
        ".log",
        ".go",
        ".rs",
        ".vue"
    }

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        """初始化工作区根目录和默认读写输出限制。"""
        self.root = Path(root or os.getcwd()).resolve()

        self.max_read_bytes: int   = 512_000
        self.max_write_bytes: int  = 1_000_000
        self.max_output_chars: int = 24_000

        self.last_shell_result: dict[str, typing.Any] | None = None
        self.validation_history: list[dict[str, typing.Any]] = []

    def relative_path(self, path: Path) -> str:
        """把路径转换为相对工作区的展示路径。"""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def walk_paths(self, base: Path, *, recursive: bool = True) -> typing.Iterator[Path]:
        """遍历工作区路径，并跳过体积较大的排除目录。"""
        if base.is_file():
            yield base
            return

        if not recursive:
            for item in base.iterdir():
                if not self.is_excluded_path(item):
                    yield item
            return

        for root, dirs, files in os.walk(base):
            root_path = Path(root)
            dirs[:] = [
                item for item in dirs
                if not self.is_excluded_path(root_path / item)
            ]
            for dirname in dirs:
                yield root_path / dirname
            for filename in files:
                item = root_path / filename
                if not self.is_excluded_path(item):
                    yield item

    def resolve_path(self, path: str | None = None) -> Path:
        """解析工作区内路径，并拒绝越过工作区边界的路径。"""
        raw       = str(path or ".").strip() or "."
        candidate = Path(raw)

        if not candidate.is_absolute():
            candidate = self.root / candidate

        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path outside workspace: {raw}")

        return resolved

    def conflict_guard(
        self,
        target: Path,
        *,
        expected_sha256: str | None,
        force: bool
    ) -> dict[str, typing.Any] | None:
        """根据可选 SHA256 基线判断目标文件是否发生外部变更。"""
        expected = str(expected_sha256 or "").strip().lower()

        if force or not expected or not target.exists():
            return None

        current = self.sha256_bytes(target.read_bytes())
        if current == expected:
            return None

        return self.fail_result(
            "file_changed_since_read",
            path=self.relative_path(target),
            expected_sha256=expected,
            current_sha256=current
        )

    def looks_text(self, path: Path) -> bool:
        """根据后缀、文件名和内容采样判断文件是否适合作为文本读取。"""
        if path.suffix.lower() in self.TEXT_SUFFIXES or path.name in {
            "README", "LICENSE", "Dockerfile", "Makefile"
        }:
            return True

        try:
            sample = path.read_bytes()[:4096]
        except OSError:
            return False

        if not sample:
            return True

        if b"\x00" in sample:
            return False

        decoded = sample.decode(const.CHARSET, const.IGNORE)
        if not decoded:
            return False

        control_count = sum(
            1 for ch in decoded
            if ord(ch) < 32 and ch not in "\t\r\n\f\b"
        )
        return control_count / max(1, len(decoded)) < 0.10

    def is_excluded_path(self, path: Path) -> bool:
        """判断路径是否位于默认排除目录中。"""
        parts = set(path.relative_to(self.root).parts) if path != self.root else set()
        return bool(parts & self.DEFAULT_EXCLUDES)

    def clip_output(self, text: str, *, max_chars: int | None = None) -> str:
        """按字符上限截断输出文本，并附加截断说明。"""
        limit = max_chars or self.max_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"

    @staticmethod
    def decode_bytes(data: bytes) -> str:
        """使用候选编码解码字节数据。"""
        return decode_process_output(data)

    @staticmethod
    def process_output_encodings() -> list[str]:
        """返回进程输出的候选解码顺序。"""
        return process_output_encodings()

    @staticmethod
    def sha256_bytes(data: bytes) -> str:
        """计算字节数据的 SHA256 摘要。"""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def ok_result(text: str, **data: typing.Any) -> dict[str, typing.Any]:
        """构造统一的成功工具返回结构。"""
        payload = {"ok": True, **data}
        if payload.get("ok") is False:
            NativeCodingBase.enrich_failure_facts(payload)
        ok = bool(payload.pop("ok"))
        return {
            "ok"          : ok,
            "text"        : text,
            "attachments" : [],
            "data"        : payload,
            "logs"        : []
        }

    @staticmethod
    def failure_context(data: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """从失败结果中提取稳定的事实上下文字段。"""
        keys = [
            "path",
            "source_path",
            "target_path",
            "cwd",
            "command",
            "resolved_command",
            "tool",
            "error",
            "exit_code",
            "timed_out",
            "line",
            "target_line",
            "hunk",
            "expected",
            "actual",
            "found",
            "expected_sha256",
            "current_sha256",
            "actual_sha256",
            "size",
            "max_bytes"
        ]

        context: dict[str, typing.Any] = {}
        for key in keys:
            if key not in data:
                continue
            value = data.get(key)
            if value is None or value == "":
                continue
            context[key] = value

        return context

    @staticmethod
    def enrich_failure_facts(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """补齐失败结果的 reason 和事实上下文，不生成修复策略。"""
        reason = str(payload.get("reason") or "operation_failed")

        payload["reason"] = reason
        if not isinstance(payload.get("failure_context"), dict):
            payload["failure_context"] = NativeCodingBase.failure_context(payload)

        return payload

    @staticmethod
    def fail_result(reason: str, **data: typing.Any) -> dict[str, typing.Any]:
        """构造统一的失败工具返回结构。"""
        payload = {"ok": False, "reason": reason, **data}
        payload.pop("ok", None)

        NativeCodingBase.enrich_failure_facts(payload)

        return {
            "ok"          : False,
            "text"        : f"native coding failed: {reason}",
            "attachments" : [],
            "data"        : payload,
            "logs"        : []
        }


class NativeCodingComponent(object):
    """共享 NativeCoding 运行时上下文的组件包装器。"""

    def __init__(self, core: NativeCodingBase) -> None:
        """保存共享的原生编码核心对象。"""
        self.core = core

    @property
    def root(self) -> Path:
        """返回共享工作区根目录。"""
        return self.core.root

    @property
    def max_read_bytes(self) -> int:
        """返回单次读取的字节上限。"""
        return self.core.max_read_bytes

    @property
    def max_write_bytes(self) -> int:
        """返回单次写入的字节上限。"""
        return self.core.max_write_bytes

    @property
    def max_output_chars(self) -> int:
        """返回命令输出的字符上限。"""
        return self.core.max_output_chars

    @property
    def last_shell_result(self) -> dict[str, typing.Any] | None:
        """返回最近一次 shell 执行结果。"""
        return self.core.last_shell_result

    @property
    def validation_history(self) -> list[dict[str, typing.Any]]:
        """返回当前会话记录的验证历史。"""
        return self.core.validation_history

    @staticmethod
    def decode_bytes(data: bytes) -> str:
        """按项目默认字符集解码字节数据。"""
        return NativeCodingBase.decode_bytes(data)

    @staticmethod
    def sha256_bytes(data: bytes) -> str:
        """计算字节数据的 SHA256 摘要。"""
        return NativeCodingBase.sha256_bytes(data)

    @staticmethod
    def ok_result(text: str, **data: typing.Any) -> dict[str, typing.Any]:
        """构造统一成功返回结构。"""
        return NativeCodingBase.ok_result(text, **data)

    @staticmethod
    def fail_result(reason: str, **data: typing.Any) -> dict[str, typing.Any]:
        """构造统一失败返回结构。"""
        return NativeCodingBase.fail_result(reason, **data)

    def relative_path(self, path: Path) -> str:
        """委托生成相对工作区路径。"""
        return self.core.relative_path(path)

    def walk_paths(self, base: Path, *, recursive: bool = True) -> typing.Iterator[Path]:
        """委托遍历工作区路径。"""
        return self.core.walk_paths(base, recursive=recursive)

    def resolve_path(self, path: str | None = None) -> Path:
        """委托解析工作区内路径。"""
        return self.core.resolve_path(path)

    def conflict_guard(
        self,
        target: Path,
        *,
        expected_sha256: str | None,
        force: bool
    ) -> dict[str, typing.Any] | None:
        """委托检查文件写入冲突。"""
        return self.core.conflict_guard(target, expected_sha256=expected_sha256, force=force)

    def looks_text(self, path: Path) -> bool:
        """委托判断文件是否适合作为文本处理。"""
        return self.core.looks_text(path)

    def is_excluded_path(self, path: Path) -> bool:
        """委托判断路径是否位于排除范围。"""
        return self.core.is_excluded_path(path)

    def clip_output(self, text: str, *, max_chars: int | None = None) -> str:
        """委托按字符上限截断文本。"""
        return self.core.clip_output(text, max_chars=max_chars)


if __name__ == '__main__':
    pass
