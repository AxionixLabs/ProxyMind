# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import typing
import asyncio
import subprocess
from pathlib import Path
from backend.mcp_code.base import NativeCodingComponent
from backend.mcp_code.exec.command_runtime import NativeCommandRuntime
from backend.utilities.process import Flux


class GitTools(NativeCodingComponent):
    """提供 git 状态和 diff 能力。"""

    @classmethod
    def _category_stats(
        cls,
        files: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        """按文件类型分类统计 diff 文件。"""
        categories = {
            "source" : [],
            "test"   : [],
            "docs"   : [],
            "config" : [],
            "asset"  : [],
            "other"  : []
        }

        for item in files:

            category = str(item.get("category") or "other")
            path     = str(item.get("path") or "")

            category_paths = categories.setdefault(category, [])
            category_paths.append(path)

        return {
            "source_files" : categories["source"],
            "test_files"   : categories["test"],
            "docs_files"   : categories["docs"],
            "config_files" : categories["config"],
            "asset_files"  : categories["asset"],
            "other_files"  : categories["other"],
            "counts"       : {key: len(value) for key, value in categories.items()}
        }

    @classmethod
    def _combine_diff_sections(
        cls,
        *,
        unstaged: dict[str, typing.Any],
        staged: dict[str, typing.Any],
        untracked: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """合并 tracked、staged 和 untracked diff 摘要。"""
        per_file = [
            *(unstaged.get("per_file") or []),
            *(staged.get("per_file") or []),
            *(untracked.get("per_file") or [])
        ]
        numeric_files = [item for item in per_file if not item.get("binary")]
        renamed_files = [
            *(unstaged.get("renamed_files") or []),
            *(staged.get("renamed_files") or [])
        ]
        hunk_summary = [
            *(unstaged.get("hunk_summary") or []),
            *(staged.get("hunk_summary") or [])
        ]

        return {
            "files"          : [str(item.get("path") or "") for item in per_file],
            "file_count"     : len(per_file),
            "added_lines"    : sum(int(item.get("added_lines") or 0) for item in numeric_files),
            "deleted_lines"  : sum(int(item.get("deleted_lines") or 0) for item in numeric_files),
            "changed_lines"  : sum(
                int(item.get("added_lines") or 0) + int(item.get("deleted_lines") or 0)
                for item in numeric_files
            ),
            "binary_files"   : [str(item.get("path") or "") for item in per_file if item.get("binary")],
            "per_file"       : per_file,
            "renamed_files"  : renamed_files,
            "rename_count"   : len(renamed_files),
            "hunk_summary"   : hunk_summary,
            "category_stats" : cls._category_stats(per_file),
            "unstaged"       : unstaged,
            "staged"         : staged,
            "untracked"      : untracked,
            "source"         : "git_diff_structured",
            "truncated"      : any(bool(section.get("truncated")) for section in (unstaged, staged, untracked))
        }

    def is_git_workspace(self) -> bool:
        """判断当前工作区是否包含 git 仓库。"""
        git_marker = self.root / ".git"
        if git_marker.exists():
            return True

        env = os.environ.copy()

        try:
            result = subprocess.run(
                NativeCommandRuntime.resolve_command(["git", "rev-parse", "--is-inside-work-tree"], env=env),
                cwd=str(self.root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        return result.returncode == 0 and (result.stdout or b"").decode(errors="ignore").strip().lower() == "true"

    def _tracked_diff_section(
        self,
        *,
        numstat_result: dict[str, typing.Any],
        name_status_result: dict[str, typing.Any],
        hunks_result: dict[str, typing.Any],
        section: str
    ) -> dict[str, typing.Any]:
        """汇总 staged 或 unstaged 的结构化 diff 信息。"""
        numstat_data = numstat_result.get("data") or {}
        name_data    = name_status_result.get("data") or {}
        hunk_data    = hunks_result.get("data") or {}

        stats        = self._parse_numstat(str(numstat_data.get("stdout") or ""))
        name_status  = self._parse_name_status(str(name_data.get("stdout") or ""))
        hunk_summary = self._parse_diff_hunks(str(hunk_data.get("stdout") or ""))

        for item in stats["per_file"]:

            path     = str(item.get("path") or "")
            metadata = name_status.get(path) or {}
            hunks    = hunk_summary.get(path) or {"path": path, "hunk_count": 0, "hunks": []}

            item.update({
                "section"      : section,
                "staged"       : section == "staged",
                "untracked"    : False,
                "change_type"  : metadata.get("change_type") or self._change_type_from_counts(item),
                "status"       : metadata.get("status"),
                "old_path"     : metadata.get("old_path") or item.get("old_path"),
                "category"     : self._file_category(path),
                "hunk_count"   : hunks.get("hunk_count", 0),
                "hunks"        : hunks.get("hunks", [])
            })

        stats["section"] = section
        stats["renamed_files"] = [
            {
                "old_path" : item.get("old_path"),
                "path"     : item.get("path"),
                "status"   : item.get("status")
            }
            for item in stats["per_file"]
            if item.get("change_type") == "renamed"
        ]
        stats["hunk_summary"] = [
            {
                "path"       : item.get("path"),
                "hunk_count" : item.get("hunk_count", 0),
                "hunks"      : item.get("hunks", [])
            }
            for item in stats["per_file"]
        ]
        stats["truncated"] = any(bool((result.get("data") or {}).get("truncated")) for result in (
            numstat_result, name_status_result, hunks_result
        ))

        return stats

    def _expand_untracked_path(
        self,
        target: Path
    ) -> list[Path]:
        """把未跟踪目录展开为文件列表。"""
        if target.is_file():
            return [target]
        if not target.is_dir():
            return []

        files: list[Path] = []
        for item in self.walk_paths(target, recursive=True):
            if item.is_file() and not self.is_excluded_path(item):
                files.append(item)
                if len(files) >= 200:
                    break
        return files

    def _untracked_file_stat(
        self,
        target: Path
    ) -> dict[str, typing.Any] | None:
        """生成未跟踪文件的行数和分类摘要。"""
        if not target.is_file():
            return None

        rel    = self.relative_path(target)
        size   = target.stat().st_size
        binary = not self.looks_text(target)

        added_lines: int | None    = None
        line_count_truncated: bool = False

        if not binary:
            raw = target.read_bytes()[:self.max_read_bytes]
            added_lines = len(self.decode_bytes(raw).splitlines())
            line_count_truncated = size > self.max_read_bytes

        return {
            "path"                 : rel,
            "added_lines"          : added_lines,
            "deleted_lines"        : 0 if not binary else None,
            "binary"               : binary,
            "section"              : "untracked",
            "staged"               : False,
            "untracked"            : True,
            "change_type"          : "untracked",
            "status"               : "??",
            "old_path"             : None,
            "category"             : self._file_category(rel),
            "size"                 : size,
            "line_count_truncated" : line_count_truncated,
            "hunk_count"           : 0,
            "hunks"                : []
        }

    async def run_git(
        self,
        args: list[str],
        *,
        output_limit: int | None = None
    ) -> dict[str, typing.Any]:
        """在工作区根目录执行 git 子命令并返回统一结果。"""
        cmd     = ["git", *args]
        workdir = self.resolve_path(".")
        env     = os.environ.copy()
        limit   = max(1, min(int(output_limit or self.max_output_chars), self.max_output_chars))
        started = time.perf_counter()

        proc = await Flux.cmd_link_exec(
            NativeCommandRuntime.resolve_command(cmd, env=env),
            cwd=str(workdir),
            env=env
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout, stderr = await proc.communicate()

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raw_stdout = self.decode_bytes(stdout or b"")
        raw_stderr = self.decode_bytes(stderr or b"")
        exit_code  = int(proc.returncode or 0)
        ok         = (exit_code == 0) and not timed_out

        data = {
            "ok"                     : ok,
            "command"                : cmd,
            "cwd"                    : self.relative_path(workdir),
            "execution_target"       : "local",
            "requires_cloud_sandbox" : False,
            "exit_code"              : exit_code,
            "timed_out"              : timed_out,
            "elapsed_ms"             : elapsed_ms,
            "output_limit"           : limit,
            "stdout"                 : self.clip_output(raw_stdout, max_chars=limit),
            "stderr"                 : self.clip_output(raw_stderr, max_chars=limit),
            "stdout_truncated"       : len(raw_stdout) > limit,
            "stderr_truncated"       : len(raw_stderr) > limit,
            "truncated"              : len(raw_stdout) > limit or len(raw_stderr) > limit
        }

        return {
            "text"        : f"git {'ok' if ok else 'failed'} exit_code={exit_code} elapsed_ms={elapsed_ms}",
            "attachments" : [],
            "data"        : data,
            "logs"        : []
        }

    async def git_status(
        self
    ) -> dict[str, typing.Any]:
        """返回当前工作区的 git status 摘要。"""
        if not self.is_git_workspace():
            return self.ok_result(
                "git status unavailable: workspace is not a git repository",
                stdout="",
                available=False
            )
        return await self.run_git(["status", "--short"])

    async def git_diff(
        self,
        path: str | None = None,
        max_chars: int = 24000
    ) -> dict[str, typing.Any]:
        """返回当前工作区或指定路径的 git diff。"""
        if not self.is_git_workspace():
            return self.ok_result(
                "git diff unavailable: workspace is not a git repository",
                stdout="",
                available=False
            )

        cmd = ["diff", "--"]
        if path:
            cmd.append(self.relative_path(self.resolve_path(path)))

        output_limit = max(1, min(int(max_chars or self.max_output_chars), self.max_output_chars))

        result = await self.run_git(cmd, output_limit=output_limit)

        data = result.get("data") or {}
        data["diff_stats"] = await self._git_diff_stats(path=path, output_limit=output_limit)

        if data.get("stdout_truncated") and output_limit < self.max_output_chars:
            data["recommended_next_steps"] = [
                {
                    "tool": "git_diff",
                    "args": {
                        "path"      : path,
                        "max_chars" : min(output_limit * 2, self.max_output_chars)
                    },
                    "reason": "increase_limit"
                }
            ]
        return result

    async def _git_diff_stats(
        self,
        *,
        path: str | None,
        output_limit: int
    ) -> dict[str, typing.Any]:
        """返回 git diff 的结构化文件、hunk 和未跟踪文件摘要。"""
        rel_path = self.relative_path(self.resolve_path(path)) if path else None

        unstaged_numstat = await self.run_git(
            ["diff", "-M", "--numstat", "--", *([rel_path] if rel_path else [])],
            output_limit=output_limit
        )
        staged_numstat = await self.run_git(
            ["diff", "--cached", "-M", "--numstat", "--", *([rel_path] if rel_path else [])],
            output_limit=output_limit
        )
        unstaged_name_status = await self.run_git(
            ["diff", "-M", "--name-status", "--", *([rel_path] if rel_path else [])],
            output_limit=output_limit
        )
        staged_name_status = await self.run_git(
            ["diff", "--cached", "-M", "--name-status", "--", *([rel_path] if rel_path else [])],
            output_limit=output_limit
        )
        unstaged_hunks = await self.run_git(
            ["diff", "-M", "--unified=0", "--", *([rel_path] if rel_path else [])],
            output_limit=output_limit
        )
        staged_hunks = await self.run_git(
            ["diff", "--cached", "-M", "--unified=0", "--", *([rel_path] if rel_path else [])],
            output_limit=output_limit
        )

        unstaged = self._tracked_diff_section(
            numstat_result=unstaged_numstat,
            name_status_result=unstaged_name_status,
            hunks_result=unstaged_hunks,
            section="unstaged"
        )
        staged = self._tracked_diff_section(
            numstat_result=staged_numstat,
            name_status_result=staged_name_status,
            hunks_result=staged_hunks,
            section="staged"
        )
        untracked = await self._untracked_diff_section(path=rel_path, output_limit=output_limit)

        return self._combine_diff_sections(
            unstaged=unstaged,
            staged=staged,
            untracked=untracked
        )

    async def _untracked_diff_section(
        self,
        *,
        path: str | None,
        output_limit: int
    ) -> dict[str, typing.Any]:
        """把未跟踪文件纳入 diff stats 摘要。"""
        cmd = ["status", "--short", "--"]
        if path:
            cmd.append(path)

        result = await self.run_git(cmd, output_limit=output_limit)

        data  = result.get("data") or {}
        files = self._untracked_status_paths(str(data.get("stdout") or ""))

        per_file: list[dict[str, typing.Any]] = []
        for rel in files[:200]:
            try:
                target = self.resolve_path(rel)
            except ValueError:
                continue
            for file_path in self._expand_untracked_path(target):
                item = self._untracked_file_stat(file_path)
                if item:
                    per_file.append(item)

        numeric_files = [item for item in per_file if not item.get("binary")]

        return {
            "files"         : [str(item.get("path") or "") for item in per_file],
            "file_count"    : len(per_file),
            "added_lines"   : sum(int(item.get("added_lines") or 0) for item in numeric_files),
            "deleted_lines" : 0,
            "changed_lines" : sum(int(item.get("added_lines") or 0) for item in numeric_files),
            "binary_files"  : [str(item.get("path") or "") for item in per_file if item.get("binary")],
            "per_file"      : per_file,
            "source"        : "untracked_files",
            "section"       : "untracked",
            "renamed_files" : [],
            "hunk_summary"  : [],
            "truncated"     : bool(data.get("truncated")) or len(files) > 200
        }

    @staticmethod
    def _parse_numstat(text: str) -> dict[str, typing.Any]:
        """解析 git diff --numstat 输出。"""
        per_file: list[dict[str, typing.Any]] = []

        for line in str(text or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue

            added_text, deleted_text, raw_path = parts[0], parts[1], parts[2]
            path_info = GitTools._normalize_numstat_path(raw_path)
            added = None if added_text == "-" else int(added_text) if added_text.isdigit() else None
            deleted = None if deleted_text == "-" else int(deleted_text) if deleted_text.isdigit() else None

            per_file.append({
                "path"          : path_info["path"],
                "old_path"      : path_info.get("old_path"),
                "raw_path"      : raw_path,
                "added_lines"   : added,
                "deleted_lines" : deleted,
                "binary"        : added is None or deleted is None
            })

        numeric_files = [item for item in per_file if not item.get("binary")]

        return {
            "files"         : [str(item.get("path") or "") for item in per_file],
            "file_count"    : len(per_file),
            "added_lines"   : sum(int(item.get("added_lines") or 0) for item in numeric_files),
            "deleted_lines" : sum(int(item.get("deleted_lines") or 0) for item in numeric_files),
            "changed_lines" : sum(
                int(item.get("added_lines") or 0) + int(item.get("deleted_lines") or 0)
                for item in numeric_files
            ),
            "binary_files" : [str(item.get("path") or "") for item in per_file if item.get("binary")],
            "per_file"     : per_file,
            "source"       : "git_numstat",
            "truncated"    : False
        }

    @staticmethod
    def _normalize_numstat_path(path: str) -> dict[str, str | None]:
        """规范化 git numstat 中的 rename 路径表示。"""
        text = str(path or "")
        if " => " not in text:
            return {"path": text, "old_path": None}

        old_path, new_path = text.split(" => ", 1)
        if "{" in old_path and "}" in new_path:
            prefix, old_tail = old_path.split("{", 1)
            new_tail, suffix = new_path.split("}", 1)
            return {
                "path": f"{prefix}{new_tail}{suffix}",
                "old_path": f"{prefix}{old_tail}{suffix}"
            }

        return {
            "path": new_path, "old_path": old_path
        }

    @staticmethod
    def _parse_name_status(text: str) -> dict[str, dict[str, typing.Any]]:
        """解析 git diff --name-status -M 输出。"""
        items: dict[str, dict[str, typing.Any]] = {}
        for line in str(text or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue

            status   = parts[0]
            code     = status[:1]
            old_path = None
            path     = parts[1]

            if code in {"R", "C"} and len(parts) >= 3:
                old_path = parts[1]
                path     = parts[2]

            items[path] = {
                "path": path,
                "old_path": old_path,
                "status": status,
                "change_type": {
                    "A": "added",
                    "M": "modified",
                    "D": "deleted",
                    "R": "renamed",
                    "C": "copied"
                }.get(code, "changed")
            }
        return items

    @staticmethod
    def _parse_diff_hunks(diff: str) -> dict[str, dict[str, typing.Any]]:
        """解析 git diff --unified=0 的文件级 hunk 摘要。"""
        files: dict[str, dict[str, typing.Any]] = {}
        current: dict[str, typing.Any] | None   = None
        current_hunks: list[dict[str, typing.Any]] = []
        hunk: dict[str, typing.Any] | None      = None

        for line in str(diff or "").splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                path  = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else parts[-1]

                current_hunks = []
                current       = {"path": path, "hunk_count": 0, "hunks": current_hunks}
                files[path]   = current
                hunk          = None

                continue

            if current is None:
                continue

            if line.startswith("rename to "):
                path = line.removeprefix("rename to ").strip()
                if path:
                    current["path"] = path
                    files[path] = current
                continue

            match = re.match(
                r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<context>.*)$",
                line
            )
            if match:
                hunk = {
                    "old_start"     : int(match.group("old_start")),
                    "old_lines"     : int(match.group("old_count") or 1),
                    "new_start"     : int(match.group("new_start")),
                    "new_lines"     : int(match.group("new_count") or 1),
                    "heading"       : match.group("context").strip(),
                    "added_lines"   : 0,
                    "deleted_lines" : 0
                }
                current_hunks.append(hunk)
                current["hunk_count"] = len(current_hunks)
                continue

            if hunk is None:
                continue
            if line.startswith("+") and not line.startswith("+++"):
                hunk["added_lines"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                hunk["deleted_lines"] += 1

        return files

    @staticmethod
    def _untracked_status_paths(status: str) -> list[str]:
        """提取 git status --short 中的未跟踪路径。"""
        paths: list[str] = []

        for line in str(status or "").splitlines():
            if not line.startswith("?? "):
                continue
            path = line[3:].strip()
            if path:
                paths.append(path)

        return paths

    @staticmethod
    def _change_type_from_counts(item: dict[str, typing.Any]) -> str:
        """根据 numstat 行数推断基础变更类型。"""
        added   = item.get("added_lines")
        deleted = item.get("deleted_lines")

        if added and not deleted:
            return "added"
        if deleted and not added:
            return "deleted"

        return "modified"

    @staticmethod
    def _file_category(path: str) -> str:
        """按路径和后缀给变更文件做粗粒度分类。"""
        normalized = str(path or "").replace("\\", "/").lower()
        name       = normalized.rsplit("/", 1)[-1]
        suffix     = Path(normalized).suffix

        if (
            normalized.startswith("test/")
            or normalized.startswith("tests/")
            or "/test/" in normalized
            or "/tests/" in normalized
            or name.startswith("test_")
            or name.endswith("_test.py")
            or ".test." in name
            or ".spec." in name
        ):
            return "test"

        if suffix in {".md", ".rst", ".txt", ".adoc"} or normalized.startswith("docs/"):
            return "docs"

        if name in {
            ".gitignore",
            "dockerfile",
            "makefile",
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "requirements.txt",
            "setup.cfg",
            "tox.ini"
        } or suffix in {".toml", ".yaml", ".yml", ".ini", ".cfg", ".json"}:
            return "config"

        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".mp4", ".mov"}:
            return "asset"

        if suffix in {
            ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs",
            ".java", ".kt", ".kts", ".swift", ".c", ".h", ".cc", ".cpp",
            ".cxx", ".hh", ".hpp", ".cs", ".vue", ".svelte", ".rb", ".php",
            ".sh", ".sql", ".css", ".scss", ".html"
        }:
            return "source"

        return "other"


if __name__ == '__main__':
    pass
