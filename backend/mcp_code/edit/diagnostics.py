# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import typing
import difflib
from pathlib import Path
from loguru import logger
from backend.mcp_code.base import NativeCodingComponent
from backend.utilities import const


class PatchDiagnostics(NativeCodingComponent):
    """提供文本补丁失败诊断和提示信息。"""

    @classmethod
    def _replacement_candidates(
        cls,
        *,
        current: str,
        old_text: str
    ) -> list[dict[str, typing.Any]]:
        """从当前文件中找出与 old_text 最接近的少量候选窗口。"""
        lines = current.splitlines(keepends=True)
        if not lines:
            return []

        old_line_count = max(1, len(old_text.splitlines()) or 1)
        window_sizes = sorted({
            max(1, old_line_count - 2),
            max(1, old_line_count - 1),
            old_line_count,
            old_line_count + 1,
            old_line_count + 2
        })

        scored: list[dict[str, typing.Any]] = []

        normalized_old = cls._normalize_patch_text_for_compare(old_text)
        newline_old    = cls._normalize_newlines(old_text)

        for window_size in window_sizes:
            if window_size > len(lines):
                continue

            for start in range(0, len(lines) - window_size + 1):

                candidate = "".join(lines[start:start + window_size])
                ratio = difflib.SequenceMatcher(None, old_text, candidate).ratio()

                if normalized_old and normalized_old == cls._normalize_patch_text_for_compare(candidate):
                    ratio = max(ratio, 0.99)
                elif newline_old == cls._normalize_newlines(candidate):
                    ratio = max(ratio, 0.97)
                if ratio < 0.45:
                    continue

                scored.append({
                    "line_start": start + 1,
                    "line_end": start + window_size,
                    "score": round(ratio, 3),
                    "line_ending_equivalent": newline_old == cls._normalize_newlines(candidate),
                    "whitespace_equivalent": (
                        bool(normalized_old)
                        and normalized_old == cls._normalize_patch_text_for_compare(candidate)
                    ),
                    "preview": cls.diagnostic_preview(candidate, limit=800)
                })

        scored.sort(key=lambda x: (-float(x["score"]), int(x["line_start"])))

        deduped: list[dict[str, typing.Any]] = []
        seen: set[tuple[int, int, str]]      = set()

        for item in scored:
            key = (
                int(item["line_start"]), int(item["line_end"]), str(item["preview"])
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= 5:
                break

        return deduped

    @staticmethod
    def _find_occurrences(text: str, needle: str, *, limit: int) -> list[int]:
        """返回 needle 在 text 中出现的前几个偏移量。"""
        if not needle:
            return []
        offsets: list[int] = []

        start = 0
        while len(offsets) < limit:
            index = text.find(needle, start)
            if index < 0:
                break
            offsets.append(index)
            start = index + max(1, len(needle))

        return offsets

    @staticmethod
    def _line_number_for_offset(text: str, offset: int) -> int:
        """把字符偏移转换为 1-based 行号。"""
        return text.count("\n", 0, max(0, offset)) + 1

    @staticmethod
    def _normalize_newlines(text: str) -> str:
        """把不同换行格式归一为 LF。"""
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _normalize_patch_text_for_compare(text: str) -> str:
        """把文本归一为空白无关的比较形式。"""
        return re.sub(r"\s+", " ", PatchDiagnostics._normalize_newlines(text).strip())

    @staticmethod
    def diagnostic_preview(value: typing.Any, *, limit: int) -> str:
        """按字符上限生成诊断预览文本。"""
        text = str(value or "")
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"

    @staticmethod
    def unified_patch_hint(reason: str) -> str:
        """根据 unified patch 失败原因返回格式提示。"""
        if reason == "unified_patch_no_files":
            return "patch must include --- and +++ file headers"
        if reason == "unified_patch_missing_new_header":
            return "each --- file header must be followed by a +++ file header"
        if reason == "unified_patch_bad_hunk_header":
            return "hunk header must look like @@ -old,count +new,count @@"
        if reason == "unified_patch_no_hunks":
            return "each file diff must include at least one @@ hunk"
        if reason == "unified_patch_bad_line":
            return "hunk body lines must start with exactly one of: space, +, -"
        if reason == "unified_patch_context_mismatch":
            return "patch context does not match the current file; read the file again and regenerate"
        if reason == "unified_patch_context_ambiguous":
            return "patch context matches multiple places; add more unique context lines"
        if reason == "unified_patch_context_out_of_range":
            return "hunk target is outside the current file; read the file again and regenerate"
        if reason == "file_changed_since_read":
            return "file sha256 changed; read the file again and retry with the current sha256"

        return "inspect failure data and regenerate the smallest valid patch"

    @staticmethod
    def unified_patch_next_action(reason: str) -> str:
        """根据 unified patch 失败原因返回建议的下一步动作。"""
        if reason in {
            "unified_patch_no_files",
            "unified_patch_missing_new_header",
            "unified_patch_bad_hunk_header",
            "unified_patch_no_hunks",
            "unified_patch_bad_line"
        }:
            return "regenerate_strict_unified_diff"
        if reason.startswith("unified_patch_context_"):
            return "read_current_context_and_regenerate_patch"
        if reason == "file_changed_since_read":
            return "refresh_file_snapshot_and_retry"
        if reason == "file_not_found":
            return "locate_file_before_editing"

        return "inspect_failure_and_retry"

    @staticmethod
    def log_patch_failure(tool: str, reason: str, data: dict[str, typing.Any]) -> None:
        """记录补丁失败的结构化诊断信息。"""
        logger.warning(
            "[PatchEngine] {} failed reason={} data={}", tool, reason, data
        )

    @staticmethod
    def clean_diff_path(path: str) -> str:
        """清理 diff 文件头中的路径前缀和附加信息。"""
        raw = str(path or "").split("\t", 1)[0].strip()
        if raw.startswith("a/") or raw.startswith("b/"):
            raw = raw[2:]
        return raw

    @staticmethod
    def read_text_preserve_newlines(target: Path) -> str:
        """读取文本文件并保留原始换行符。"""
        with target.open("r", encoding=const.CHARSET, errors=const.IGNORE, newline="") as handle:
            return handle.read()

    @staticmethod
    def refresh_written_file_mtime(target: Path) -> None:
        """刷新已写文件的修改时间，便于后续状态检测。"""
        try:
            stat = target.stat()
            fresh_mtime_ns = max(stat.st_mtime_ns + 1_000_000_000, time.time_ns() + 1_000_000_000)
            os.utime(target, ns=(stat.st_atime_ns, fresh_mtime_ns))
        except OSError:
            return

    def replacement_mismatch_diagnostics(
        self,
        *,
        current: str,
        old_text: str
    ) -> dict[str, typing.Any]:
        """为精确替换失败返回可用于重试的近似匹配诊断。"""
        normalized_old     = self._normalize_patch_text_for_compare(old_text)
        normalized_current = self._normalize_patch_text_for_compare(current)
        newline_old        = self._normalize_newlines(old_text)
        newline_current    = self._normalize_newlines(current)

        actual_occurrences = [
            {"line": self._line_number_for_offset(current, index)}
            for index in self._find_occurrences(current, old_text, limit=8)
        ]

        return {
            "line_ending_equivalent": bool(old_text not in current and newline_old in newline_current),
            "whitespace_equivalent": bool(
                old_text not in current and normalized_old and normalized_old in normalized_current
            ),
            "actual_occurrences": actual_occurrences,
            "replacement_candidates": self._replacement_candidates(
                current=current,
                old_text=old_text
            )
        }

    def with_unified_patch_diagnostics(
        self,
        *,
        reason: str,
        data: dict[str, typing.Any],
        patch: str
    ) -> dict[str, typing.Any]:
        """为 unified patch 失败结果补充格式提示和补丁预览。"""
        enriched = dict(data)
        enriched.setdefault("patch_format_hint", self.unified_patch_hint(reason))
        enriched.setdefault("suggested_next_action", self.unified_patch_next_action(reason))
        enriched.setdefault("patch_preview", self.diagnostic_preview(patch, limit=1600))
        return enriched


if __name__ == '__main__':
    pass
