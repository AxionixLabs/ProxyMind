# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import typing
import hashlib
from pathlib import Path
from backend.utilities import const


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
        ".json",
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
        ".rs"
    }

    DANGEROUS_COMMANDS = {
        "rm",
        "rmdir",
        "del",
        "erase",
        "format",
        "mkfs",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "diskpart",
        "remove-item",
        "ri",
        "rd"
    }

    REVIEW_COMMANDS = {
        "pip",
        "pip3",
        "npm",
        "pnpm",
        "yarn",
        "bun",
        "curl",
        "wget",
        "chmod",
        "chown"
    }

    REVIEW_GIT_SUBCOMMANDS = {
        "add",
        "am",
        "apply",
        "bisect",
        "branch",
        "checkout",
        "cherry-pick",
        "clean",
        "commit",
        "fetch",
        "merge",
        "mv",
        "pull",
        "push",
        "rebase",
        "reset",
        "restore",
        "revert",
        "rm",
        "stash",
        "switch",
        "tag",
        "update-index",
        "worktree"
    }

    CONTROL_OPERATORS = {
        ";",
        "&&",
        "||",
        "|",
        ">",
        ">>",
        "<",
        "$(",
        "`"
    }

    def __init__(self, root: str | None = None):
        """初始化工作区根目录和默认读写输出限制。"""
        self.root = Path(root or os.getcwd()).resolve()

        self.max_read_bytes: int   = 512_000
        self.max_write_bytes: int  = 1_000_000
        self.max_output_chars: int = 24_000

    def _rel(self, path: Path) -> str:
        """把路径转换为相对工作区的展示路径。"""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def _walk(self, base: Path, *, recursive: bool = True) -> typing.Iterator[Path]:
        """遍历工作区路径，并跳过体积较大的排除目录。"""
        if base.is_file():
            yield base
            return

        if not recursive:
            for item in base.iterdir():
                if not self._is_excluded(item):
                    yield item
            return

        for root, dirs, files in os.walk(base):
            root_path = Path(root)
            dirs[:] = [
                item for item in dirs
                if not self._is_excluded(root_path / item)
            ]
            for dirname in dirs:
                yield root_path / dirname
            for filename in files:
                item = root_path / filename
                if not self._is_excluded(item):
                    yield item

    def _resolve(self, path: str | None = None) -> Path:
        """解析工作区内路径，并拒绝越过工作区边界的路径。"""
        raw       = str(path or ".").strip() or "."
        candidate = Path(raw)

        if not candidate.is_absolute():
            candidate = self.root / candidate

        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path outside workspace: {raw}")

        return resolved

    def _looks_text(self, path: Path) -> bool:
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

    def _is_excluded(self, path: Path) -> bool:
        """判断路径是否位于默认排除目录中。"""
        parts = set(path.relative_to(self.root).parts) if path != self.root else set()
        return bool(parts & self.DEFAULT_EXCLUDES)

    def _clip_output(self, text: str, *, max_chars: int | None = None) -> str:
        """按字符上限截断输出文本，并附加截断说明。"""
        limit = max_chars or self.max_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"

    @staticmethod
    def _decode(data: bytes) -> str:
        """按项目默认字符集解码字节数据。"""
        return data.decode(const.CHARSET, const.IGNORE)

    @staticmethod
    def _sha256(data: bytes) -> str:
        """计算字节数据的 SHA256 摘要。"""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _ok(text: str, **data: typing.Any) -> dict[str, typing.Any]:
        """构造统一的成功工具返回结构。"""
        payload = {"ok": True, **data}
        return {
            "text"        : text,
            "attachments" : [],
            "data"        : payload,
            "logs"        : []
        }

    @staticmethod
    def _fail(reason: str, **data: typing.Any) -> dict[str, typing.Any]:
        """构造统一的失败工具返回结构。"""
        payload = {"ok": False, "reason": reason, **data}
        return {
            "text"        : f"native coding failed: {reason}",
            "attachments" : [],
            "data"        : payload,
            "logs"        : []
        }


class NativeCodingComponent(object):
    """共享 NativeCoding 运行时上下文的组件包装器。"""

    def __init__(self, core: NativeCodingBase):
        """保存共享的原生编码核心对象。"""
        self.core = core

    def __getattr__(self, name: str) -> typing.Any:
        """把未在组件上定义的属性代理到核心对象。"""
        return getattr(self.core, name)


if __name__ == '__main__':
    pass
