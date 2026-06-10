# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import typing
import fnmatch
from pathlib import Path
from backend.mcp_code.base import NativeCodingComponent


class WorkspaceSearchDiagnostics(NativeCodingComponent):

    def __init__(self, core):
        super().__init__(core)

    def search_coverage_diagnostics(
        self,
        *,
        base: typing.Any,
        glob: str | None,
        text_search_enabled: bool,
        content_diagnostics: dict[str, typing.Any],
        symbol_metadata: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """描述 workspace_search 未覆盖或只部分覆盖的文件范围。"""
        diagnostics: dict[str, typing.Any] = {
            "complete": True,
            "incomplete": False,
            "reasons": [],
            "files_considered": 0,
            "files_scanned": int(content_diagnostics.get("files_scanned") or 0),
            "search_byte_limit": self.max_read_bytes,
            "glob_excluded_files": [],
            "glob_excluded_file_count": 0,
            "binary_or_non_text_files": [],
            "binary_or_non_text_file_count": 0,
            "generated_or_excluded_dirs": [],
            "generated_or_excluded_dir_count": 0,
            "large_files_truncated": list(content_diagnostics.get("large_files_truncated") or []),
            "large_files_truncated_count": len(content_diagnostics.get("large_files_truncated") or []),
            "symbol_truncated_files": list(symbol_metadata.get("truncated_files") or []),
            "symbol_truncated_file_count": len(symbol_metadata.get("truncated_files") or []),
            "reference_search_truncated_files": list(symbol_metadata.get("reference_search_truncated_files") or []),
            "reference_search_truncated_file_count": len(symbol_metadata.get("reference_search_truncated_files") or [])
        }

        self._collect_excluded_dir_diagnostics(base, diagnostics)

        for item in self.walk_paths(base, recursive=True):
            if not item.is_file():
                continue

            rel = self.relative_path(item)
            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                diagnostics["glob_excluded_file_count"] += 1
                self._append_limited(
                    diagnostics["glob_excluded_files"],
                    {
                        "path"   : rel,
                        "reason" : "glob_mismatch"
                    }
                )
                continue

            diagnostics["files_considered"] += 1

            if text_search_enabled and not self.looks_text(item):
                diagnostics["binary_or_non_text_file_count"] += 1
                self._append_limited(
                    diagnostics["binary_or_non_text_files"],
                    {
                        "path"   : rel,
                        "size"   : item.stat().st_size,
                        "reason" : "file_not_text"
                    }
                )

        reason_checks = {
            "large_file_tail_not_searched"       : bool(diagnostics["large_files_truncated"]),
            "symbol_index_file_truncated"        : bool(diagnostics["symbol_truncated_files"]),
            "reference_search_file_truncated"    : bool(diagnostics["reference_search_truncated_files"]),
            "binary_or_non_text_skipped"         : diagnostics["binary_or_non_text_file_count"] > 0,
            "generated_or_excluded_dirs_skipped" : diagnostics["generated_or_excluded_dir_count"] > 0,
            "glob_scope_excluded_files"          : diagnostics["glob_excluded_file_count"] > 0
        }

        diagnostics["reasons"]    = [reason for reason, enabled in reason_checks.items() if enabled]
        diagnostics["incomplete"] = bool(diagnostics["reasons"])
        diagnostics["complete"]   = not diagnostics["incomplete"]

        diagnostics["recommended_next_steps"] = self._coverage_next_steps(diagnostics=diagnostics)
        return diagnostics

    def _collect_excluded_dir_diagnostics(
        self,
        base: typing.Any,
        diagnostics: dict[str, typing.Any]
    ) -> None:
        """采样会被默认遍历排除的目录。"""
        if not base.is_dir():
            return
        for root, dirs, _files in os.walk(base):
            root_path = Path(root)
            kept_dirs: list[str] = []
            for dirname in dirs:
                candidate = root_path / dirname
                if self.is_excluded_path(candidate):
                    diagnostics["generated_or_excluded_dir_count"] += 1
                    self._append_limited(
                        diagnostics["generated_or_excluded_dirs"],
                        {
                            "path"   : self.relative_path(candidate),
                            "reason" : "default_exclude"
                        }
                    )
                    continue
                kept_dirs.append(dirname)
            dirs[:] = kept_dirs

    @staticmethod
    def _append_limited(
        items: list[dict[str, typing.Any]],
        item: dict[str, typing.Any],
        *,
        limit: int = 50
    ) -> None:
        """向诊断样本列表追加有限条目。"""
        if len(items) < limit:
            items.append(item)

    @staticmethod
    def _coverage_next_steps(
        *,
        diagnostics: dict[str, typing.Any]
    ) -> list[dict[str, typing.Any]]:
        """为搜索覆盖不足生成补查建议。"""
        steps: list[dict[str, typing.Any]] = []

        for item in diagnostics.get("large_files_truncated") or []:
            path = str(item.get("path") or "")
            if not path:
                continue
            steps.append({
                "tool": "workspace_read_file",
                "args": {
                    "path": path,
                    "start_line": 1,
                    "max_lines": 200
                },
                "reason": "large_file_tail_not_searched_read_windows"
            })
            if len(steps) >= 5:
                break

        for item in diagnostics.get("binary_or_non_text_files") or []:
            path = str(item.get("path") or "")
            if not path:
                continue
            steps.append({
                "tool": "shell_exec",
                "args": {
                    "command": ["file", path],
                    "cwd": "."
                },
                "reason": "inspect_binary_or_non_text_file_type"
            })
            if len(steps) >= 8:
                break

        return steps[:8]

    @staticmethod
    def merge_symbol_search_metadata(
        metadata: dict[str, typing.Any],
        data: dict[str, typing.Any]
    ) -> None:
        """把内部符号索引诊断合并到 workspace_search 返回。"""
        for key in (
            "indexed_files",
            "skipped_files",
            "truncated_files",
            "supported_languages",
            "references",
            "call_candidates",
            "reference_search_truncated_files"
        ):
            existing = metadata.setdefault(key, [])
            incoming = data.get(key) or []
            if not isinstance(existing, list) or not isinstance(incoming, list):
                continue
            if key == "supported_languages":
                metadata[key] = sorted({*(str(item) for item in existing), *(str(item) for item in incoming)})
                continue
            seen = {
                (
                    str(item.get("path") or ""),
                    str(item.get("line") or ""),
                    str(item.get("symbol") or item.get("name") or item.get("reason") or "")
                )
                for item in existing
                if isinstance(item, dict)
            }
            for item in incoming:
                if not isinstance(item, dict):
                    continue
                marker = (
                    str(item.get("path") or ""),
                    str(item.get("line") or ""),
                    str(item.get("symbol") or item.get("name") or item.get("reason") or "")
                )
                if marker in seen:
                    continue
                seen.add(marker)
                existing.append(item)

        metadata["indexed_file_count"]   = len(metadata.get("indexed_files") or [])
        metadata["skipped_file_count"]   = len(metadata.get("skipped_files") or [])
        metadata["truncated_file_count"] = len(metadata.get("truncated_files") or [])
        metadata["reference_count"]      = len(metadata.get("references") or [])
        metadata["call_candidate_count"] = len(metadata.get("call_candidates") or [])

        metadata["reference_search_truncated_file_count"] = len(metadata.get("reference_search_truncated_files") or [])
        metadata["parser_level"] = data.get("parser_level") or metadata.get("parser_level") or "regex"

        limitations = [str(item) for item in (metadata.get("parser_limitations") or [])]
        for item in data.get("parser_limitations") or []:
            value = str(item)
            if value not in limitations:
                limitations.append(value)
        metadata["parser_limitations"] = limitations


if __name__ == '__main__':
    pass
