# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
from backend.mcp_code.base import NativeCodingComponent


class UnifiedPatchParser(NativeCodingComponent):

    def __init__(self, core, *, diagnostics):
        super().__init__(core)
        self._diagnostics = diagnostics

    def parse_unified_patch(
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

            old_path = self._diagnostics.clean_diff_path(line[4:].strip())
            i += 1
            if i >= len(lines) or not lines[i].startswith("+++ "):
                return {
                    "ok"     : False,
                    "reason" : "unified_patch_missing_new_header",
                    "data"   : {"old_path": old_path}
                }

            new_path = self._diagnostics.clean_diff_path(lines[i][4:].strip())
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


if __name__ == '__main__':
    pass
