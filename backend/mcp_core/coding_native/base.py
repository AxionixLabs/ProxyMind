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

    LOOP_TOOLS = {
        "workspace_root",
        "workspace_list_files",
        "workspace_read_file",
        "workspace_search_text",
        "repo_map",
        "repo_find_symbol",
        "workspace_write_file",
        "workspace_copy_file",
        "workspace_move_file",
        "workspace_delete_file",
        "workspace_apply_patch",
        "workspace_apply_unified_patch",
        "shell_exec",
        "git_status",
        "git_diff",
        "change_summary",
        "rollback_run",
        "native_plan",
        "record_sandbox_result"
    }

    def __init__(self, root: str | None = None):
        self.root = Path(root or os.getcwd()).resolve()

        self.max_read_bytes   = 512_000
        self.max_write_bytes  = 1_000_000
        self.max_output_chars = 24_000

        self.sessions: dict[str, dict[str, typing.Any]] = {}

    def _rel(self, path: Path) -> str:
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
        raw = str(path or ".").strip() or "."
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path outside workspace: {raw}")
        return resolved

    def _looks_text(self, path: Path) -> bool:
        return path.suffix.lower() in self.TEXT_SUFFIXES or path.name in {
            "README", "LICENSE", "Dockerfile", "Makefile"
        }

    def _is_excluded(self, path: Path) -> bool:
        parts = set(path.relative_to(self.root).parts) if path != self.root else set()
        return bool(parts & self.DEFAULT_EXCLUDES)

    def _clip_output(self, text: str, *, max_chars: int | None = None) -> str:
        limit = max_chars or self.max_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"

    @staticmethod
    def _decode(data: bytes) -> str:
        return data.decode(const.CHARSET, const.IGNORE)

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _ok(text: str, **data: typing.Any) -> dict[str, typing.Any]:
        payload = {"ok": True, **data}
        return {
            "text"        : text,
            "attachments" : [],
            "data"        : payload,
            "logs"        : []
        }

    @staticmethod
    def _fail(reason: str, **data: typing.Any) -> dict[str, typing.Any]:
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
        self.core = core

    def __getattr__(self, name: str) -> typing.Any:
        return getattr(self.core, name)


if __name__ == '__main__':
    pass
