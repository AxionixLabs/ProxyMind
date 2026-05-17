# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
from pathlib import Path
from backend.mcp_core.native_coding.base import NativeCodingComponent
from backend.utilities import const

class PatchEngine(NativeCodingComponent):

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
        target = self._resolve(path)
        if not target.is_file():
            return self._fail("file_not_found", path=path)
        if conflict := self._conflict_guard(target, expected_sha256=expected_sha256, force=force):
            return conflict
        current = target.read_text(encoding=const.CHARSET, errors=const.IGNORE)
        count = current.count(old_text)
        expected = max(1, int(expected_replacements or 1))
        if count != expected:
            return self._fail(
                "replacement_count_mismatch",
                path=self._rel(target),
                found=count,
                expected=expected
            )
        updated = current.replace(old_text, new_text, expected)
        size = len(updated.encode(const.CHARSET, const.IGNORE))
        if size > self.max_write_bytes:
            return self._fail("content_too_large", size=size, max_bytes=self.max_write_bytes)
        target.write_text(updated, encoding=const.CHARSET)
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
        planned_result = self._plan_unified_patch(
            patch=patch,
            expected_sha256=expected_sha256,
            force=force
        )
        if not planned_result.get("ok"):
            data = dict(planned_result.get("data") or {})
            data.pop("reason", None)
            return self._fail(planned_result["reason"], **data)
        planned = planned_result["planned"]

        for item in planned:
            if item["action"] == "delete":
                item["target"].unlink()
                continue
            item["target"].parent.mkdir(parents=True, exist_ok=True)
            item["target"].write_text(item["content"], encoding=const.CHARSET)

        return self._ok(
            f"workspace unified patch ok files={len(planned)} hunks={sum(item['hunks'] for item in planned)}",
            files=[
                {
                    "path": item["path"],
                    "action": item["action"],
                    "hunks": item["hunks"],
                    "relocated_hunks": item["relocated_hunks"],
                    "sha256": item["sha256"]
                }
                for item in planned
            ],
            file_count=len(planned),
            hunk_count=sum(item["hunks"] for item in planned),
            relocated_hunk_count=sum(len(item["relocated_hunks"]) for item in planned)
        )

    def _conflict_guard(
        self,
        target: Path,
        *,
        expected_sha256: str | None,
        force: bool
    ) -> dict[str, typing.Any] | None:
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

    def _plan_unified_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        parsed = self._parse_unified_patch(patch)
        if not parsed.get("ok"):
            return {
                "ok": False,
                "reason": parsed["reason"],
                "data": parsed.get("data") or {}
            }

        expected_map = {
            str(k).strip(): str(v).strip().lower()
            for k, v in (expected_sha256 or {}).items()
            if str(k).strip() and str(v).strip()
        }
        planned: list[dict[str, typing.Any]] = []

        for item in parsed["files"]:
            path = str(item["path"])
            action = str(item.get("action") or "modify")
            target = self._resolve(path)
            if action == "create" and target.exists():
                return {"ok": False, "reason": "file_already_exists", "data": {"path": path}}
            if action in {"modify", "delete"} and not target.is_file():
                return {"ok": False, "reason": "file_not_found", "data": {"path": path}}
            if action in {"modify", "delete"}:
                expected = expected_map.get(path) or expected_map.get(self._rel(target))
                if conflict := self._conflict_guard(target, expected_sha256=expected, force=force):
                    return {
                        "ok": False,
                        "reason": (conflict.get("data") or {}).get("reason") or "file_changed_since_read",
                        "data": conflict.get("data") or {}
                    }
                current = target.read_text(encoding=const.CHARSET, errors=const.IGNORE)
            else:
                current = ""

            applied = self._apply_unified_hunks(current, item["hunks"])
            if not applied.get("ok"):
                data = {"path": path, **(applied.get("data") or {})}
                return {"ok": False, "reason": applied["reason"], "data": data}
            content = str(applied["content"])
            if action == "delete" and content:
                return {
                    "ok": False,
                    "reason": "unified_patch_delete_leaves_content",
                    "data": {"path": path}
                }
            size = len(content.encode(const.CHARSET, const.IGNORE))
            if size > self.max_write_bytes:
                return {
                    "ok": False,
                    "reason": "content_too_large",
                    "data": {"path": path, "size": size, "max_bytes": self.max_write_bytes}
                }
            planned.append({
                "path": path,
                "action": action,
                "target": target,
                "content": content,
                "hunks": len(item["hunks"]),
                "relocated_hunks": list(applied.get("relocated_hunks") or []),
                "sha256": self._sha256(content.encode(const.CHARSET, const.IGNORE))
            })

        return {"ok": True, "planned": planned}

    def _parse_unified_patch(self, patch: str) -> dict[str, typing.Any]:
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
                return {"ok": False, "reason": "unified_patch_missing_new_header", "data": {"old_path": old_path}}

            new_path = self._clean_diff_path(lines[i][4:].strip())
            path = new_path if new_path != "/dev/null" else old_path
            if old_path == "/dev/null" and new_path == "/dev/null":
                return {"ok": False, "reason": "unified_patch_bad_file_header", "data": {}}
            if old_path == "/dev/null":
                action = "create"
            elif new_path == "/dev/null":
                action = "delete"
            else:
                action = "modify"
            i += 1

            hunks: list[dict[str, typing.Any]] = []
            while i < len(lines) and not lines[i].startswith("--- "):
                if not lines[i].startswith("@@ "):
                    i += 1
                    continue
                header = lines[i]
                match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$", header)
                if not match:
                    return {"ok": False, "reason": "unified_patch_bad_hunk_header", "data": {"header": header}}
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
                                "ok": False,
                                "reason": "unified_patch_no_newline_without_line",
                                "data": {"header": header}
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
                        "marker": marker,
                        "text": item[1:],
                        "no_newline": False
                    })
                    i += 1
                if old_seen != old_count or new_seen != new_count:
                    return {
                        "ok": False,
                        "reason": "unified_patch_hunk_count_mismatch",
                        "data": {
                            "header": header,
                            "old_expected": old_count,
                            "old_found": old_seen,
                            "new_expected": new_count,
                            "new_found": new_seen
                        }
                    }
                hunks.append({
                    "old_start": old_start,
                    "new_start": new_start,
                    "old_count": old_count,
                    "new_count": new_count,
                    "lines": body
                })

            if not hunks:
                return {"ok": False, "reason": "unified_patch_no_hunks", "data": {"path": path}}
            files.append({"path": path, "action": action, "hunks": hunks})

        if not files:
            return {"ok": False, "reason": "unified_patch_no_files", "data": {}}
        return {"ok": True, "files": files}

    @staticmethod

    def _clean_diff_path(path: str) -> str:
        raw = str(path or "").split("\t", 1)[0].strip()
        if raw.startswith("a/") or raw.startswith("b/"):
            raw = raw[2:]
        return raw

    def _apply_unified_hunks(
        self,
        content: str,
        hunks: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        original = content.splitlines(keepends=True)
        output: list[str] = []
        cursor = 0
        relocated_hunks: list[dict[str, int]] = []

        for hunk_index, hunk in enumerate(hunks, start=1):
            old_start = int(hunk.get("old_start") if hunk.get("old_start") is not None else 1)
            old_count = int(hunk.get("old_count") if hunk.get("old_count") is not None else 1)
            target_index = self._hunk_target_index(old_start=old_start, old_count=old_count)
            if target_index < cursor:
                return {
                    "ok": False,
                    "reason": "unified_patch_overlapping_hunk",
                    "data": {"hunk": hunk_index}
                }

            old_sequence = self._hunk_old_sequence(hunk)
            if old_sequence and not self._lines_match_at(original, target_index, old_sequence):
                located = self._locate_hunk(original, old_sequence, cursor=cursor)
                if located.get("ok"):
                    relocated_index = int(located["index"])
                    if relocated_index < cursor:
                        return {
                            "ok": False,
                            "reason": "unified_patch_overlapping_hunk",
                            "data": {"hunk": hunk_index, "target_line": relocated_index + 1}
                        }
                    relocated_hunks.append({
                        "hunk": hunk_index,
                        "from_line": target_index + 1,
                        "to_line": relocated_index + 1
                    })
                    target_index = relocated_index
                else:
                    data = located.get("data") or {}
                    data.update({
                        "hunk": hunk_index,
                        "target_line": target_index + 1,
                        "expected_sequence": [
                            item.rstrip("\n") for item in old_sequence[:12]
                        ],
                        "nearby": self._nearby_lines(original, target_index)
                    })
                    return {
                        "ok": False,
                        "reason": located.get("reason") or "unified_patch_context_mismatch",
                        "data": data
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
                expected_line = self._patch_line_content(text, no_newline=no_newline)
                if marker in {" ", "-"}:
                    if cursor >= len(original):
                        return {
                            "ok": False,
                            "reason": "unified_patch_context_out_of_range",
                            "data": {"hunk": hunk_index, "line": body_index, "expected": text}
                        }
                    current_line = original[cursor]
                    if current_line != expected_line:
                        return {
                            "ok": False,
                            "reason": "unified_patch_context_mismatch",
                            "data": {
                                "hunk": hunk_index,
                                "line": body_index,
                                "expected": expected_line.rstrip("\n"),
                                "actual": current_line.rstrip("\n")
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
            "ok": True,
            "content": "".join(output),
            "relocated_hunks": relocated_hunks
        }

    @staticmethod

    def _patch_line_content(text: str, *, no_newline: bool = False) -> str:
        return text if no_newline else f"{text}\n"

    @staticmethod

    def _hunk_target_index(*, old_start: int, old_count: int) -> int:
        if old_start <= 0:
            return 0
        if old_count == 0:
            return old_start
        return old_start - 1

    def _hunk_old_sequence(self, hunk: dict[str, typing.Any]) -> list[str]:
        sequence: list[str] = []
        for raw_line in hunk.get("lines") or []:
            if isinstance(raw_line, dict):
                marker = str(raw_line.get("marker") or "")
                text = str(raw_line.get("text") or "")
                no_newline = bool(raw_line.get("no_newline"))
            else:
                raw_text = str(raw_line)
                marker = raw_text[0] if raw_text else ""
                text = raw_text[1:]
                no_newline = False
            if marker in {" ", "-"}:
                sequence.append(self._patch_line_content(text, no_newline=no_newline))
        return sequence

    @staticmethod

    def _lines_match_at(lines: list[str], index: int, expected: list[str]) -> bool:
        if index < 0 or index + len(expected) > len(lines):
            return False
        return lines[index:index + len(expected)] == expected

    def _locate_hunk(
        self,
        lines: list[str],
        expected: list[str],
        *,
        cursor: int
    ) -> dict[str, typing.Any]:
        if not expected:
            return {"ok": False, "reason": "unified_patch_context_empty", "data": {}}
        max_start = len(lines) - len(expected)
        if max_start < cursor:
            return {
                "ok": False,
                "reason": "unified_patch_context_out_of_range",
                "data": {}
            }
        candidates: list[int] = []
        for index in range(max(0, cursor), max_start + 1):
            if self._lines_match_at(lines, index, expected):
                candidates.append(index)
                if len(candidates) > 8:
                    break
        if not candidates:
            return {
                "ok": False,
                "reason": "unified_patch_context_mismatch",
                "data": {}
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

    @staticmethod

    def _nearby_lines(lines: list[str], index: int, radius: int = 3) -> list[dict[str, typing.Any]]:
        if not lines:
            return []
        start = max(0, index - radius)
        end = min(len(lines), index + radius + 1)
        return [
            {
                "line": item + 1,
                "text": lines[item].rstrip("\n")
            }
            for item in range(start, end)
        ]


if __name__ == '__main__':
    pass
