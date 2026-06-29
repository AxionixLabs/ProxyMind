# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)


class PatchApplier(NativeCodingComponent):
    """应用已解析的 patch hunk。"""

    def __init__(self, core: NativeCodingBase, *, diagnostics: typing.Any) -> None:
        """保存共享运行时上下文和补丁诊断依赖。"""
        super().__init__(core)

        self._diagnostics = diagnostics

    @staticmethod
    def _patch_line_content(text: str, *, no_newline: bool = False, newline: str = "\n") -> str:
        """按 hunk 行标记生成带目标换行符的文本行。"""
        return text if no_newline else f"{text}{newline}"

    @staticmethod
    def _hunk_target_index(*, old_start: int, old_count: int) -> int:
        """把 hunk 的 1-based 起始行转换为 0-based 应用位置。"""
        if old_start <= 0:
            return 0
        if old_count == 0:
            return old_start
        return old_start - 1

    @staticmethod
    def _lines_match_at(lines: list[str], index: int, expected: list[str]) -> bool:
        """判断指定位置的连续行是否与期望序列完全一致。"""
        if index < 0 or index + len(expected) > len(lines):
            return False
        return lines[index:index + len(expected)] == expected

    @staticmethod
    def _nearby_lines(lines: list[str], index: int, radius: int = 3) -> list[dict[str, typing.Any]]:
        """返回指定位置附近的行号和文本。"""
        if not lines:
            return []

        start = max(0, index - radius)
        end   = min(len(lines), index + radius + 1)

        return [
            {
                "line" : item + 1,
                "text" : lines[item].rstrip("\r\n")
            }
            for item in range(start, end)
        ]

    @staticmethod
    def _strip_line_ending(line: str) -> str:
        """移除单行末尾的 CR/LF 换行符。"""
        return str(line).rstrip("\r\n")

    @staticmethod
    def _detect_newline(lines: list[str]) -> str:
        """根据现有文本行推断主要换行符。"""
        for line in lines:
            if line.endswith("\r\n"):
                return "\r\n"
            if line.endswith("\n"):
                return "\n"

        return "\n"

    @staticmethod
    def _hunk_overlap_is_context_only(
        hunk: dict[str, typing.Any],
        *,
        overlap_count: int
    ) -> bool:
        """判断 hunk 开头重叠的旧文本是否只包含上下文行。"""
        if overlap_count <= 0:
            return True

        consumed = 0
        for raw_line in hunk.get("lines") or []:
            if isinstance(raw_line, dict):
                marker = str(raw_line.get("marker") or "")
            else:
                marker = str(raw_line)[0] if str(raw_line) else ""

            if marker not in {" ", "-"}:
                continue
            consumed += 1
            if marker != " ":
                return False
            if consumed >= overlap_count:
                return True

        return consumed >= overlap_count

    def _hunk_old_sequence(
        self,
        hunk: dict[str, typing.Any],
        *,
        newline: str = "\n"
    ) -> list[str]:
        """提取 hunk 中需要与原文匹配的上下文和删除行序列。"""
        sequence: list[str] = []
        for raw_line in hunk.get("lines") or []:
            if isinstance(raw_line, dict):
                marker     = str(raw_line.get("marker") or "")
                text       = str(raw_line.get("text") or "")
                no_newline = bool(raw_line.get("no_newline"))
            else:
                raw_text   = str(raw_line)
                marker     = raw_text[0] if raw_text else ""
                text       = raw_text[1:]
                no_newline = False

            if marker in {" ", "-"}:
                sequence.append(
                    self._patch_line_content(text, no_newline=no_newline, newline=newline)
                )

        return sequence

    def _locate_hunk(
        self,
        lines: list[str],
        expected: list[str],
        *,
        cursor: int,
        allow_overlap: bool = False
    ) -> dict[str, typing.Any]:
        """在当前文本中查找可唯一匹配的 hunk 上下文位置。"""
        if not expected:
            return {
                "ok"     : False,
                "reason" : "patch_context_empty",
                "data"   : {}
            }

        max_start    = len(lines) - len(expected)
        search_start = cursor

        if allow_overlap:
            search_start = max(0, cursor - len(expected))
        if max_start < search_start:
            return {
                "ok"     : False,
                "reason" : "patch_context_out_of_range",
                "data"   : {}
            }
        candidates: list[int] = []
        for index in range(max(0, search_start), max_start + 1):
            if self._lines_match_at(lines, index, expected):
                candidates.append(index)
                if len(candidates) > 8:
                    break
        if not candidates:
            return {
                "ok"     : False,
                "reason" : "patch_context_mismatch",
                "data"   : {}
            }
        if len(candidates) > 1:
            return {
                "ok": False,
                "reason": "patch_context_ambiguous",
                "data": {
                    "candidate_lines": [item + 1 for item in candidates[:8]]
                }
            }
        return {"ok": True, "index": candidates[0]}

    def apply_patch_hunks(
        self,
        content: str,
        hunks: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        """把已解析的 hunk 应用到文本内容并返回新内容。"""
        original = content.splitlines(keepends=True)

        output: list[str] = []
        cursor: int       = 0

        relocated_hunks: list[dict[str, int]] = []

        newline = self._detect_newline(original)

        for hunk_index, hunk in enumerate(hunks, start=1):

            old_start    = int(hunk.get("old_start") if hunk.get("old_start") is not None else 1)
            old_count    = int(hunk.get("old_count") if hunk.get("old_count") is not None else 1)
            target_index = self._hunk_target_index(old_start=old_start, old_count=old_count)

            old_sequence = self._hunk_old_sequence(hunk, newline=newline)
            if old_sequence and (
                target_index < cursor
                or not self._lines_match_at(original, target_index, old_sequence)
            ):
                located = self._locate_hunk(
                    original,
                    old_sequence,
                    cursor=cursor,
                    allow_overlap=True
                )
                if located.get("ok"):
                    relocated_index = int(located["index"])
                    overlap_count = max(0, cursor - relocated_index)
                    if (
                        relocated_index < cursor
                        and not self._hunk_overlap_is_context_only(
                            hunk,
                            overlap_count=overlap_count
                        )
                    ):
                        return {
                            "ok": False,
                            "reason": "patch_overlapping_hunk",
                            "data": {
                                "hunk"        : hunk_index,
                                "hunk_header" : hunk.get("header"),
                                "target_line" : relocated_index + 1
                            }
                        }
                    relocated_hunks.append({
                        "hunk"      : hunk_index,
                        "from_line" : target_index + 1,
                        "to_line"   : relocated_index + 1
                    })
                    target_index = relocated_index
                else:
                    data = located.get("data") or {}
                    data.update({
                        "hunk": hunk_index,
                        "hunk_header": hunk.get("header"),
                        "target_line": target_index + 1,
                        "expected_sequence": [
                            self._strip_line_ending(item) for item in old_sequence[:12]
                        ],
                        "actual_sequence": [
                            self._strip_line_ending(item)
                            for item in original[target_index:target_index + len(old_sequence)]
                        ],
                        "nearby": self._nearby_lines(original, target_index)
                    })
                    return {
                        "ok"     : False,
                        "reason" : located.get("reason") or "patch_context_mismatch",
                        "data"   : data
                    }
            elif target_index < cursor:
                return {
                    "ok": False,
                    "reason": "patch_overlapping_hunk",
                    "data": {
                        "hunk"        : hunk_index,
                        "hunk_header" : hunk.get("header")
                    }
                }

            overlap_remaining = max(0, cursor - target_index)
            if target_index >= cursor:
                output.extend(original[cursor:target_index])
                cursor = target_index

            for body_index, raw_line in enumerate(hunk.get("lines") or [], start=1):
                if isinstance(raw_line, dict):
                    marker     = str(raw_line.get("marker") or "")
                    text       = str(raw_line.get("text") or "")
                    no_newline = bool(raw_line.get("no_newline"))
                else:
                    marker     = str(raw_line)[0]
                    text       = str(raw_line)[1:]
                    no_newline = False

                expected_line = self._patch_line_content(text, no_newline=no_newline, newline=newline)

                if marker in {" ", "-"}:
                    if overlap_remaining > 0:
                        if marker != " ":
                            return {
                                "ok": False,
                                "reason": "patch_overlapping_hunk",
                                "data": {
                                    "hunk"        : hunk_index,
                                    "hunk_header" : hunk.get("header"),
                                    "line"        : body_index,
                                    "target_line" : cursor + 1
                                }
                            }
                        overlap_remaining -= 1
                        continue

                    if cursor >= len(original):
                        return {
                            "ok": False,
                            "reason": "patch_context_out_of_range",
                            "data": {
                                "hunk"        : hunk_index,
                                "hunk_header" : hunk.get("header"),
                                "line"        : body_index,
                                "target_line" : cursor + 1,
                                "expected"    : text,
                                "nearby"      : self._nearby_lines(original, cursor)
                            }
                        }

                    current_line = original[cursor]
                    if current_line != expected_line:
                        return {
                            "ok": False,
                            "reason": "patch_context_mismatch",
                            "data": {
                                "hunk"              : hunk_index,
                                "hunk_header"       : hunk.get("header"),
                                "line"              : body_index,
                                "target_line"       : cursor + 1,
                                "expected"          : self._strip_line_ending(expected_line),
                                "actual"            : self._strip_line_ending(current_line),
                                "expected_sequence" : [self._strip_line_ending(expected_line)],
                                "actual_sequence"   : [self._strip_line_ending(current_line)],
                                "nearby"            : self._nearby_lines(original, cursor)
                            }
                        }
                    cursor += 1
                    if marker == " ":
                        output.append(current_line)
                    continue

                if marker == "+":
                    output.append(expected_line)

        output.extend(original[cursor:])

        return {
            "ok"              : True,
            "content"         : "".join(output),
            "relocated_hunks" : relocated_hunks
        }


if __name__ == '__main__':
    pass
