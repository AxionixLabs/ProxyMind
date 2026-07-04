# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing


class PatchParser(object):
    """解析严格 apply_patch 文本为结构化补丁数据。"""

    @staticmethod
    def parse_patch(
        patch: str
    ) -> dict[str, typing.Any]:
        """把严格 apply_patch 文本解析为文件和 hunk 的结构化表示。"""
        lines = str(patch or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()

        if not lines or lines[0] != "*** Begin Patch":
            return {"ok": False, "reason": "native_patch_missing_begin", "data": {}}
        if lines[-1:] != ["*** End Patch"]:
            return {"ok": False, "reason": "native_patch_missing_end", "data": {}}

        files: list[dict[str, typing.Any]]    = []
        current: dict[str, typing.Any] | None = None

        hunk_state: dict[str, dict[str, typing.Any] | None] = {"current": None}

        seen_paths: set[str] = set()

        def close_hunk() -> None:
            current_hunk = hunk_state["current"]
            if current is not None and current_hunk is not None:
                hunks = current.get("hunks")
                if isinstance(hunks, list):
                    hunks.append(current_hunk)
            hunk_state["current"] = None

        def ensure_hunk() -> dict[str, typing.Any]:
            current_hunk = hunk_state["current"]
            if current_hunk is None:
                current_hunk = {
                    "header"             : "@@",
                    "old_start"          : 1,
                    "new_start"          : 1,
                    "old_count"          : 0,
                    "new_count"          : 0,
                    "declared_old_count" : 0,
                    "declared_new_count" : 0,
                    "count_corrected"    : False,
                    "lines"              : []
                }
                hunk_state["current"] = current_hunk
            return current_hunk

        for line in lines[1:-1]:
            if line.startswith("*** Add File: "):
                close_hunk()
                path = line[len("*** Add File: "):].strip()
                if not path:
                    return {"ok": False, "reason": "native_patch_missing_path", "data": {"line": line}}
                if path in seen_paths:
                    return {"ok": False, "reason": "native_patch_duplicate_file", "data": {"path": path}}
                seen_paths.add(path)

                current = {
                    "path"     : path,
                    "old_path" : "/dev/null",
                    "new_path" : path,
                    "action"   : "create",
                    "hunks"    : []
                }
                files.append(current)
                continue

            if line.startswith("*** Update File: "):
                close_hunk()
                path = line[len("*** Update File: "):].strip()
                if not path:
                    return {"ok": False, "reason": "native_patch_missing_path", "data": {"line": line}}
                if path in seen_paths:
                    return {"ok": False, "reason": "native_patch_duplicate_file", "data": {"path": path}}
                seen_paths.add(path)

                current = {
                    "path"     : path,
                    "old_path" : path,
                    "new_path" : path,
                    "action"   : "modify",
                    "hunks"    : []
                }
                files.append(current)
                continue

            if line.startswith("*** Delete File: "):
                close_hunk()
                path = line[len("*** Delete File: "):].strip()
                if not path:
                    return {"ok": False, "reason": "native_patch_missing_path", "data": {"line": line}}
                if path in seen_paths:
                    return {"ok": False, "reason": "native_patch_duplicate_file", "data": {"path": path}}
                seen_paths.add(path)

                current = {
                    "path"     : path,
                    "old_path" : path,
                    "new_path" : "/dev/null",
                    "action"   : "delete",
                    "hunks"    : []
                }
                files.append(current)
                continue

            if line.startswith("*** Move to: "):
                if current is None:
                    return {"ok": False, "reason": "native_patch_move_without_file", "data": {"line": line}}
                path = line[len("*** Move to: "):].strip()
                if not path:
                    return {"ok": False, "reason": "native_patch_missing_path", "data": {"line": line}}
                current["path"] = path
                current["new_path"] = path
                current["action"] = "rename"
                continue

            if line.startswith("*** "):
                return {"ok": False, "reason": "native_patch_unexpected_control_line", "data": {"line": line}}

            if current is None:
                if not line.strip():
                    continue
                return {"ok": False, "reason": "native_patch_content_without_file", "data": {"line": line}}

            if line.startswith("@@"):
                close_hunk()
                hunk_state["current"] = {
                    "header"             : line,
                    "old_start"          : 1,
                    "new_start"          : 1,
                    "old_count"          : 0,
                    "new_count"          : 0,
                    "declared_old_count" : 0,
                    "declared_new_count" : 0,
                    "count_corrected"    : False,
                    "lines"              : []
                }
                continue

            if line == r"\ No newline at end of file":
                hunk = ensure_hunk()
                if not hunk["lines"]:
                    return {"ok": False, "reason": "native_patch_no_newline_without_line", "data": {"line": line}}
                hunk["lines"][-1]["no_newline"] = True
                continue

            if not line:
                return {"ok": False, "reason": "native_patch_bad_line", "data": {"line": line}}

            marker = line[0]
            if marker not in {" ", "+", "-"}:
                return {"ok": False, "reason": "native_patch_bad_line", "data": {"line": line}}
            if current.get("action") == "create" and marker != "+":
                return {
                    "ok"     : False,
                    "reason" : "native_patch_bad_create_line",
                    "data"   : {"path": current.get("path"), "line": line}
                }

            hunk = ensure_hunk()
            if marker in {" ", "-"}:
                hunk["old_count"] += 1
            if marker in {" ", "+"}:
                hunk["new_count"] += 1
            hunk["declared_old_count"] = hunk["old_count"]
            hunk["declared_new_count"] = hunk["new_count"]

            hunk["lines"].append({
                "marker"     : marker,
                "text"       : line[1:],
                "no_newline" : False
            })

        close_hunk()

        if not files:
            return {"ok": False, "reason": "native_patch_no_files", "data": {}}
        for item in files:
            if item.get("action") == "delete":
                continue
            if not item["hunks"]:
                return {
                    "ok"     : False,
                    "reason" : "native_patch_no_hunks",
                    "data"   : {"path": item["path"]}
                }

        return {"ok": True, "files": files}


if __name__ == '__main__':
    pass
