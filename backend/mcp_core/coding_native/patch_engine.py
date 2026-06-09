# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import typing
import difflib
from pathlib import Path
from loguru import logger
from backend.mcp_core.coding_native.base import NativeCodingComponent
from backend.utilities import const


class PatchEngine(NativeCodingComponent):
    """提供工作区文本补丁解析、校验、应用和诊断能力。"""

    def apply_patch(
        self,
        *,
        path: str,
        old_text: str,
        new_text: str,
        expected_replacements: int = 1,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """对单个文本文件执行精确片段替换。"""
        target = self._resolve(path)

        if not target.is_file():
            return self._fail("file_not_found", path=path)
        if not str(old_text or ""):
            return self._fail("old_text_empty", path=self._rel(target))
        if conflict := self._conflict_guard(target, expected_sha256=expected_sha256, force=force):
            return conflict

        current  = target.read_text(encoding=const.CHARSET, errors=const.IGNORE)
        count    = current.count(old_text)
        expected = max(1, int(expected_replacements or 1))

        if count != expected:
            diagnostics = self._replacement_mismatch_diagnostics(
                current=current,
                old_text=old_text
            )
            data = {
                "path"                  : self._rel(target),
                "found"                 : count,
                "expected"              : expected,
                "old_text_preview"      : self._diagnostic_preview(old_text, limit=600),
                "current_preview"       : self._diagnostic_preview(current, limit=1200),
                **diagnostics,
                "suggested_next_action" : "refresh_file_snapshot_or_use_write_file"
            }
            self._log_patch_failure(
                "workspace_apply_patch", "replacement_count_mismatch", data
            )
            return self._fail(
                "replacement_count_mismatch", **data
            )

        updated = current.replace(old_text, new_text, expected)
        size    = len(updated.encode(const.CHARSET, const.IGNORE))

        if size > self.max_write_bytes:
            return self._fail(
                "content_too_large", size=size, max_bytes=self.max_write_bytes
            )

        target.write_text(updated, encoding=const.CHARSET, newline="")

        return self._ok(
            f"workspace patch ok path={self._rel(target)} replacements={expected}",
            path=self._rel(target),
            replacements=expected,
            sha256=self._sha256(updated.encode(const.CHARSET, const.IGNORE))
        )

    def apply_unified_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """解析并应用标准 unified diff 补丁。"""
        planned_result = self._plan_unified_patch(
            patch=patch,
            expected_sha256=expected_sha256,
            force=force
        )
        if not planned_result.get("ok"):
            data = dict(planned_result.get("data") or {})
            data.pop("reason", None)
            data = self._with_unified_patch_diagnostics(
                reason=str(planned_result["reason"]),
                data=data,
                patch=patch
            )
            self._log_patch_failure(
                "workspace_apply_unified_patch", str(planned_result["reason"]), data
            )
            return self._fail(planned_result["reason"], **data)

        planned = planned_result["planned"]
        for item in planned:
            if item["action"] == "delete":
                item["target"].unlink()
                continue
            item["target"].parent.mkdir(parents=True, exist_ok=True)
            item["target"].write_text(item["content"], encoding=const.CHARSET, newline="")
            self._refresh_written_file_mtime(item["target"])
            if item["action"] == "rename" and item.get("source_target"):
                item["source_target"].unlink()

        changed_files = [
            self._public_unified_patch_file(item) for item in planned
        ]
        created_files = [
            item for item in changed_files if item.get("action") == "create"
        ]
        updated_files = [
            item for item in changed_files if item.get("action") == "modify"
        ]
        deleted_files = [
            item for item in changed_files if item.get("action") == "delete"
        ]

        return self._ok(
            f"workspace unified patch ok files={len(planned)} hunks={sum(item['hunks'] for item in planned)}",
            files=[
                {
                    "path"            : item["path"],
                    "source_path"     : item["source_path"],
                    "action"          : item["action"],
                    "hunks"           : item["hunks"],
                    "relocated_hunks" : item["relocated_hunks"],
                    "corrected_hunks" : item["corrected_hunks"],
                    "sha256"          : item["sha256"],
                    "sha256_before"   : item["sha256_before"],
                    "sha256_after"    : item["sha256_after"],
                    "added_lines"     : item["added_lines"],
                    "removed_lines"   : item["removed_lines"],
                    "replacements"    : item["replacements"]
                }
                for item in planned
            ],
            changed_files=changed_files,
            created_files=created_files,
            updated_files=updated_files,
            deleted_files=deleted_files,
            file_count=len(planned),
            hunk_count=sum(item["hunks"] for item in planned),
            added_lines=sum(item["added_lines"] for item in planned),
            removed_lines=sum(item["removed_lines"] for item in planned),
            replacements=sum(item["replacements"] for item in planned),
            relocated_hunk_count=sum(len(item["relocated_hunks"]) for item in planned),
            corrected_hunk_count=sum(len(item["corrected_hunks"]) for item in planned)
        )

    def _conflict_guard(
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

        current = self._sha256(target.read_bytes())
        if current == expected:
            return None

        return self._fail(
            "file_changed_since_read",
            path=self._rel(target),
            expected_sha256=expected,
            current_sha256=current
        )

    def _replacement_mismatch_diagnostics(
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

    def _plan_unified_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False,
        virtual_files: dict[str, str | None] | None = None
    ) -> dict[str, typing.Any]:
        """预检查 unified diff，并生成待写入文件的变更计划。"""
        parsed = self._parse_unified_patch(patch)
        if not parsed.get("ok"):
            return {
                "ok"     : False,
                "reason" : parsed["reason"],
                "data"   : parsed.get("data") or {}
            }

        expected_map = {
            str(k).strip(): str(v).strip().lower()
            for k, v in (expected_sha256 or {}).items()
            if str(k).strip() and str(v).strip()
        }
        planned: list[dict[str, typing.Any]] = []

        seen_paths: set[str] = set()

        for item in parsed["files"]:

            path   = str(item["path"])
            action = str(item.get("action") or "modify")

            try:
                target = self._resolve(path)
            except ValueError as exc:
                return {
                    "ok": False,
                    "reason": "path_outside_workspace",
                    "data": {
                        "path": path,
                        "error": str(exc),
                        "suggested_next_action": "regenerate_patch_with_workspace_relative_paths"
                    }
                }

            rel           = self._rel(target)
            source_target = target
            source_rel    = rel

            if action == "rename":
                source_path = str(item.get("old_path") or "")
                try:
                    source_target = self._resolve(source_path)
                except ValueError as exc:
                    return {
                        "ok": False,
                        "reason": "path_outside_workspace",
                        "data": {
                            "path": source_path,
                            "error": str(exc),
                            "suggested_next_action": "regenerate_patch_with_workspace_relative_paths"
                        }
                    }
                source_rel = self._rel(source_target)

            duplicate_paths = [rel]
            if action == "rename":
                duplicate_paths.append(source_rel)
            duplicate = next((item_path for item_path in duplicate_paths if item_path in seen_paths), "")
            if duplicate:
                return {
                    "ok": False,
                    "reason": "unified_patch_duplicate_file",
                    "data": {
                        "path": duplicate,
                        "suggested_next_action": "merge_changes_into_single_file_diff"
                    }
                }
            seen_paths.update(duplicate_paths)

            has_virtual     = rel in (virtual_files or {})
            virtual_content = (virtual_files or {}).get(rel)
            virtual_exists  = has_virtual and virtual_content is not None
            disk_exists     = target.is_file()
            exists          = virtual_exists if has_virtual else disk_exists

            if action == "create" and exists:
                return {
                    "ok": False,
                    "reason": "file_already_exists",
                    "data": {
                        "path": path,
                        "suggested_next_action": "use_modify_patch_or_choose_new_path"
                    }
                }
            if action == "rename":
                if not source_target.is_file():
                    return {
                        "ok": False,
                        "reason": "file_not_found",
                        "data": {
                            "path": source_rel,
                            "suggested_next_action": "list_or_read_workspace_then_regenerate_patch"
                        }
                    }
                if target.exists():
                    return {
                        "ok": False,
                        "reason": "file_already_exists",
                        "data": {
                            "path": rel,
                            "suggested_next_action": "use_modify_patch_or_choose_new_path"
                        }
                    }
            if action in {"modify", "delete"} and not exists:
                return {
                    "ok": False,
                    "reason": "file_not_found",
                    "data": {
                        "path": path,
                        "suggested_next_action": "list_or_read_workspace_then_regenerate_patch"
                    }
                }
            if action in {"modify", "delete", "rename"}:
                expected = expected_map.get(path) or expected_map.get(rel)
                if action == "rename":
                    expected = expected or expected_map.get(source_rel) or expected_map.get(str(item.get("old_path") or ""))
                    if conflict := self._conflict_guard(source_target, expected_sha256=expected, force=force):
                        return {
                            "ok"     : False,
                            "reason" : (conflict.get("data") or {}).get("reason") or "file_changed_since_read",
                            "data"   : conflict.get("data") or {}
                        }
                    current = self._read_text_preserve_newlines(source_target)
                elif has_virtual:
                    current = str(virtual_content or "")
                    current_sha256 = self._sha256(current.encode(const.CHARSET, const.IGNORE))
                    if expected and not force and expected != current_sha256:
                        return {
                            "ok": False,
                            "reason": "file_changed_since_read",
                            "data": {
                                "path": rel,
                                "expected_sha256": expected,
                                "current_sha256": current_sha256,
                                "suggested_next_action": "refresh_file_snapshot_and_retry_with_current_sha256"
                            }
                        }
                else:
                    if conflict := self._conflict_guard(target, expected_sha256=expected, force=force):
                        return {
                            "ok"     : False,
                            "reason" : (conflict.get("data") or {}).get("reason") or "file_changed_since_read",
                            "data"   : conflict.get("data") or {}
                        }
                    current = self._read_text_preserve_newlines(target)
            else:
                current = ""

            sha256_before = (
                self._sha256(current.encode(const.CHARSET, const.IGNORE))
                if action in {"modify", "delete", "rename"} else None
            )

            applied = self._apply_unified_hunks(current, item["hunks"])
            if not applied.get("ok"):
                reason = str(applied["reason"])
                data   = {"path": path, **(applied.get("data") or {})}
                data   = self._with_unified_patch_diagnostics(reason=reason, data=data, patch=patch)

                return {
                    "ok"     : False,
                    "reason" : reason,
                    "data"   : data
                }

            content = str(applied["content"])
            if action == "delete" and content:
                return {
                    "ok": False,
                    "reason": "unified_patch_delete_leaves_content",
                    "data": {
                        "path": path,
                        "suggested_next_action": "regenerate_delete_patch_with_all_original_lines"
                    }
                }
            size = len(content.encode(const.CHARSET, const.IGNORE))
            if size > self.max_write_bytes:
                return {
                    "ok": False,
                    "reason": "content_too_large",
                    "data": {
                        "path": path,
                        "size": size,
                        "max_bytes": self.max_write_bytes,
                        "suggested_next_action": "split_change_or_reduce_generated_content"
                    }
                }

            line_stats      = self._unified_patch_line_stats(item["hunks"])
            corrected_hunks = self._unified_patch_count_corrections(item["hunks"])
            sha256_content  = self._sha256(content.encode(const.CHARSET, const.IGNORE))

            planned.append({
                "path"            : path,
                "source_path"     : source_rel if action == "rename" else None,
                "action"          : action,
                "target"          : target,
                "source_target"   : source_target if action == "rename" else None,
                "content"         : content,
                "hunks"           : len(item["hunks"]),
                "relocated_hunks" : list(applied.get("relocated_hunks") or []),
                "corrected_hunks" : corrected_hunks,
                "sha256"          : sha256_content,
                "sha256_before"   : sha256_before,
                "sha256_after"    : None if action == "delete" else sha256_content,
                **line_stats
            })

        return {"ok": True, "planned": planned}

    def _parse_unified_patch(
        self,
        patch: str
    ) -> dict[str, typing.Any]:
        """把 unified diff 文本解析为文件和 hunk 的结构化表示。"""
        lines = str(patch or "").splitlines()
        files: list[dict[str, typing.Any]] = []
        i = 0

        while i < len(lines):
            line = lines[i]
            if not line.startswith("--- "):
                i += 1
                continue

            old_path = self._clean_diff_path(line[4:].strip())
            i += 1
            if i >= len(lines) or not lines[i].startswith("+++ "):
                return {
                    "ok"     : False,
                    "reason" : "unified_patch_missing_new_header",
                    "data"   : {"old_path": old_path}
                }

            new_path = self._clean_diff_path(lines[i][4:].strip())
            if old_path == "/dev/null" and new_path == "/dev/null":
                return {"ok": False, "reason": "unified_patch_bad_file_header", "data": {}}
            if old_path == "/dev/null":
                action = "create"
                path = new_path
            elif new_path == "/dev/null":
                action = "delete"
                path = old_path
            elif old_path != new_path:
                action = "rename"
                path = new_path
            else:
                action = "modify"
                path = new_path
            i += 1

            hunks: list[dict[str, typing.Any]] = []
            while i < len(lines) and not lines[i].startswith("--- "):
                if not lines[i].startswith("@@ "):
                    i += 1
                    continue

                header = lines[i]
                match  = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$", header)

                if not match:
                    return {
                        "ok"     : False,
                        "reason" : "unified_patch_bad_hunk_header",
                        "data"   : {"header": header}
                    }

                old_start = int(match.group(1))
                old_count = int(match.group(2) if match.group(2) is not None else 1)
                new_start = int(match.group(3))
                new_count = int(match.group(4) if match.group(4) is not None else 1)
                i += 1

                body: list[dict[str, typing.Any]] = []
                old_seen = 0
                new_seen = 0

                while i < len(lines) and not lines[i].startswith("@@ ") and not lines[i].startswith("--- "):
                    item = lines[i]
                    if item == r"\ No newline at end of file":
                        if not body:
                            return {
                                "ok"     : False,
                                "reason" : "unified_patch_no_newline_without_line",
                                "data"   : {"header": header}
                            }
                        body[-1]["no_newline"] = True
                        i += 1
                        continue
                    if not item:
                        return {"ok": False, "reason": "unified_patch_bad_line", "data": {"line": item}}
                    if item[0] not in {" ", "+", "-"}:
                        return {"ok": False, "reason": "unified_patch_bad_line", "data": {"line": item}}
                    marker = item[0]
                    if marker in {" ", "-"}:
                        old_seen += 1
                    if marker in {" ", "+"}:
                        new_seen += 1

                    body.append({
                        "marker"     : marker,
                        "text"       : item[1:],
                        "no_newline" : False
                    })
                    i += 1

                count_corrected = old_seen != old_count or new_seen != new_count
                hunks.append({
                    "header"             : header,
                    "old_start"          : old_start,
                    "new_start"          : new_start,
                    "old_count"          : old_seen,
                    "new_count"          : new_seen,
                    "declared_old_count" : old_count,
                    "declared_new_count" : new_count,
                    "count_corrected"    : count_corrected,
                    "lines"              : body
                })

            if not hunks:
                return {
                    "ok"     : False,
                    "reason" : "unified_patch_no_hunks",
                    "data"   : {"path": path}
                }

            files.append({
                "path"     : path,
                "old_path" : old_path,
                "new_path" : new_path,
                "action"   : action,
                "hunks"    : hunks
            })

        if not files:
            return {
                "ok"     : False,
                "reason" : "unified_patch_no_files",
                "data"   : {}
            }

        return {
            "ok"    : True,
            "files" : files
        }

    def _apply_unified_hunks(
        self,
        content: str,
        hunks: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        """把已解析的 hunk 应用到文本内容并返回新内容。"""
        original = content.splitlines(keepends=True)
        output: list[str] = []
        cursor: int = 0
        relocated_hunks: list[dict[str, int]] = []

        newline = self._detect_newline(original)

        for hunk_index, hunk in enumerate(hunks, start=1):

            old_start    = int(hunk.get("old_start") if hunk.get("old_start") is not None else 1)
            old_count    = int(hunk.get("old_count") if hunk.get("old_count") is not None else 1)
            target_index = self._hunk_target_index(old_start=old_start, old_count=old_count)

            if target_index < cursor:
                return {
                    "ok": False,
                    "reason": "unified_patch_overlapping_hunk",
                    "data": {
                        "hunk": hunk_index,
                        "hunk_header": hunk.get("header")
                    }
                }

            old_sequence = self._hunk_old_sequence(hunk, newline=newline)
            if old_sequence and not self._lines_match_at(original, target_index, old_sequence):
                located = self._locate_hunk(original, old_sequence, cursor=cursor)
                if located.get("ok"):
                    relocated_index = int(located["index"])
                    if relocated_index < cursor:
                        return {
                            "ok": False,
                            "reason": "unified_patch_overlapping_hunk",
                            "data": {
                                "hunk": hunk_index,
                                "hunk_header": hunk.get("header"),
                                "target_line": relocated_index + 1
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
                        "patch_format_hint": self._unified_patch_hint(
                            str(located.get("reason") or "unified_patch_context_mismatch")
                        ),
                        "suggested_next_action": self._unified_patch_next_action(
                            str(located.get("reason") or "unified_patch_context_mismatch")
                        ),
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
                        "reason" : located.get("reason") or "unified_patch_context_mismatch",
                        "data"   : data
                    }

            output.extend(original[cursor:target_index])
            cursor = target_index

            for body_index, raw_line in enumerate(hunk.get("lines") or [], start=1):
                if isinstance(raw_line, dict):
                    marker = str(raw_line.get("marker") or "")
                    text = str(raw_line.get("text") or "")
                    no_newline = bool(raw_line.get("no_newline"))
                else:
                    marker = str(raw_line)[0]
                    text = str(raw_line)[1:]
                    no_newline = False
                expected_line = self._patch_line_content(text, no_newline=no_newline, newline=newline)
                if marker in {" ", "-"}:
                    if cursor >= len(original):
                        return {
                            "ok": False,
                            "reason": "unified_patch_context_out_of_range",
                            "data": {
                                "hunk": hunk_index,
                                "hunk_header": hunk.get("header"),
                                "line": body_index,
                                "target_line": cursor + 1,
                                "expected": text,
                                "nearby": self._nearby_lines(original, cursor),
                                "patch_format_hint": self._unified_patch_hint("unified_patch_context_out_of_range"),
                                "suggested_next_action": self._unified_patch_next_action("unified_patch_context_out_of_range")
                            }
                        }
                    current_line = original[cursor]
                    if current_line != expected_line:
                        return {
                            "ok": False,
                            "reason": "unified_patch_context_mismatch",
                            "data": {
                                "hunk": hunk_index,
                                "hunk_header": hunk.get("header"),
                                "line": body_index,
                                "target_line": cursor + 1,
                                "expected": self._strip_line_ending(expected_line),
                                "actual": self._strip_line_ending(current_line),
                                "expected_sequence": [self._strip_line_ending(expected_line)],
                                "actual_sequence": [self._strip_line_ending(current_line)],
                                "nearby": self._nearby_lines(original, cursor),
                                "patch_format_hint": self._unified_patch_hint("unified_patch_context_mismatch"),
                                "suggested_next_action": self._unified_patch_next_action("unified_patch_context_mismatch")
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
        cursor: int
    ) -> dict[str, typing.Any]:
        """在当前文本中查找可唯一匹配的 hunk 上下文位置。"""
        if not expected:
            return {
                "ok"     : False,
                "reason" : "unified_patch_context_empty",
                "data"   : {}
            }
        max_start = len(lines) - len(expected)
        if max_start < cursor:
            return {
                "ok"     : False,
                "reason" : "unified_patch_context_out_of_range",
                "data"   : {}
            }
        candidates: list[int] = []
        for index in range(max(0, cursor), max_start + 1):
            if self._lines_match_at(lines, index, expected):
                candidates.append(index)
                if len(candidates) > 8:
                    break
        if not candidates:
            return {
                "ok"     : False,
                "reason" : "unified_patch_context_mismatch",
                "data"   : {}
            }
        if len(candidates) > 1:
            return {
                "ok": False,
                "reason": "unified_patch_context_ambiguous",
                "data": {
                    "candidate_lines": [item + 1 for item in candidates[:8]]
                }
            }
        return {"ok": True, "index": candidates[0]}

    def _with_unified_patch_diagnostics(
        self,
        *,
        reason: str,
        data: dict[str, typing.Any],
        patch: str
    ) -> dict[str, typing.Any]:
        """为 unified patch 失败结果补充格式提示和补丁预览。"""
        enriched = dict(data)
        enriched.setdefault("patch_format_hint", self._unified_patch_hint(reason))
        enriched.setdefault("suggested_next_action", self._unified_patch_next_action(reason))
        enriched.setdefault("patch_preview", self._diagnostic_preview(patch, limit=1600))
        return enriched

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
                    "preview": cls._diagnostic_preview(candidate, limit=800)
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
        return re.sub(r"\s+", " ", PatchEngine._normalize_newlines(text).strip())

    @staticmethod
    def _diagnostic_preview(value: typing.Any, *, limit: int) -> str:
        """按字符上限生成诊断预览文本。"""
        text = str(value or "")
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"

    @staticmethod
    def _unified_patch_hint(reason: str) -> str:
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
    def _unified_patch_next_action(reason: str) -> str:
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
    def _log_patch_failure(tool: str, reason: str, data: dict[str, typing.Any]) -> None:
        """记录补丁失败的结构化诊断信息。"""
        logger.warning(
            "[PatchEngine] {} failed reason={} data={}", tool, reason, data
        )

    @staticmethod
    def _clean_diff_path(path: str) -> str:
        """清理 diff 文件头中的路径前缀和附加信息。"""
        raw = str(path or "").split("\t", 1)[0].strip()
        if raw.startswith("a/") or raw.startswith("b/"):
            raw = raw[2:]
        return raw

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
    def _unified_patch_line_stats(hunks: list[dict[str, typing.Any]]) -> dict[str, int]:
        """统计 unified patch hunk 中的新增、删除和上下文行数。"""
        added   = 0
        removed = 0
        context = 0

        for hunk in hunks:
            for raw_line in hunk.get("lines") or []:
                marker = str(raw_line.get("marker") or "") if isinstance(raw_line, dict) else str(raw_line)[:1]
                if marker == "+":
                    added += 1
                elif marker == "-":
                    removed += 1
                elif marker == " ":
                    context += 1

        return {
            "added_lines"   : added,
            "removed_lines" : removed,
            "context_lines" : context,
            "replacements"  : min(added, removed)
        }

    @staticmethod
    def _unified_patch_count_corrections(hunks: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
        """收集 hunk 头声明行数与实际行数不一致的修正信息。"""
        corrections: list[dict[str, typing.Any]] = []
        for index, hunk in enumerate(hunks, start=1):
            if not bool(hunk.get("count_corrected")):
                continue
            corrections.append({
                "hunk"               : index,
                "header"             : hunk.get("header"),
                "declared_old_count" : hunk.get("declared_old_count"),
                "declared_new_count" : hunk.get("declared_new_count"),
                "actual_old_count"   : hunk.get("old_count"),
                "actual_new_count"   : hunk.get("new_count")
            })
        return corrections

    @staticmethod
    def _public_unified_patch_file(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """把内部变更计划转换为对外返回的文件摘要。"""
        return {
            "path"            : item.get("path"),
            "source_path"     : item.get("source_path"),
            "action"          : item.get("action"),
            "hunks"           : item.get("hunks"),
            "relocated_hunks" : list(item.get("relocated_hunks") or []),
            "corrected_hunks" : list(item.get("corrected_hunks") or []),
            "sha256_before"   : item.get("sha256_before"),
            "sha256_after"    : item.get("sha256_after"),
            "added_lines"     : item.get("added_lines"),
            "removed_lines"   : item.get("removed_lines"),
            "replacements"    : item.get("replacements")
        }

    @staticmethod
    def _read_text_preserve_newlines(target: Path) -> str:
        """读取文本文件并保留原始换行符。"""
        with target.open("r", encoding=const.CHARSET, errors=const.IGNORE, newline="") as handle:
            return handle.read()

    @staticmethod
    def _refresh_written_file_mtime(target: Path) -> None:
        """刷新已写文件的修改时间，便于后续状态检测。"""
        try:
            stat = target.stat()
            fresh_mtime_ns = max(stat.st_mtime_ns + 1_000_000_000, time.time_ns() + 1_000_000_000)
            os.utime(target, ns=(stat.st_atime_ns, fresh_mtime_ns))
        except OSError:
            return


if __name__ == '__main__':
    pass
