# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import time
import typing
import difflib
from pathlib import Path
from mind_app.native_coding.base import NativeCodingComponent
from metadata import const


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

        diagnostics = {
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
        return diagnostics

    def with_patch_diagnostics(
        self,
        *,
        data: dict[str, typing.Any],
        patch: str
    ) -> dict[str, typing.Any]:
        """为 patch 失败结果补充补丁预览。"""
        enriched = dict(data)
        enriched.setdefault("patch_preview", self.diagnostic_preview(patch, limit=1600))
        return enriched


if __name__ == '__main__':
    pass

