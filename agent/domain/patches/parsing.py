# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.patches.models import (
    PatchAction,
    PatchFile,
    PatchHunk,
    PatchLine,
    PatchMarker,
    PatchParseFailure,
    PatchParseResult,
    PatchParseSuccess,
)
from agent.domain.patches.unified import parse_unified_patch


class PatchParser(object):
    """识别并解析受支持的文本补丁格式。"""

    @staticmethod
    def _failure(reason: str, **data: typing.Any) -> PatchParseFailure:
        """构造补丁解析失败结果。"""
        return {"ok": False, "reason": reason, "data": data}

    @staticmethod
    def _new_hunk(header: str = "@@") -> PatchHunk:
        """创建不依赖声明行号的严格格式 hunk。"""
        return PatchHunk(header=header)

    @staticmethod
    def _new_file(
        *,
        path: str,
        old_path: str,
        new_path: str,
        action: PatchAction
    ) -> PatchFile:
        """创建满足统一契约的文件补丁节点。"""
        return PatchFile(
            path=path,
            old_path=old_path,
            new_path=new_path,
            action=action
        )

    @staticmethod
    def _renamed_file(current: PatchFile, new_path: str) -> PatchFile:
        """基于当前节点创建重命名后的完整文件补丁节点。"""
        return PatchFile(
            path=new_path,
            old_path=current.old_path,
            new_path=new_path,
            action="rename",
            hunks=current.hunks
        )

    @staticmethod
    def _append_hunk(current: PatchFile | None, hunk: PatchHunk | None) -> None:
        """把已完成的 hunk 追加到当前文件。"""
        if current is not None and hunk is not None:
            current.hunks.append(hunk)

    @staticmethod
    def parse_patch(
        patch: str
    ) -> PatchParseResult:
        """识别格式并返回统一的文件和 hunk 结构。"""
        lines = str(patch or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        while lines and lines[0] == "":
            lines.pop(0)

        if not lines:
            return PatchParser._failure("native_patch_no_files")
        if lines[0] == "*** Begin Patch":
            return PatchParser._parse_strict_patch(lines)
        if lines[0].startswith(("diff --git ", "--- ")):
            return parse_unified_patch("\n".join(lines))
        return PatchParser._failure(
            "native_patch_unsupported_format",
            line=lines[0]
        )

    @staticmethod
    def _parse_strict_patch(lines: list[str]) -> PatchParseResult:
        """解析带显式控制行的补丁文本。"""
        if lines[-1:] != ["*** End Patch"]:
            return PatchParser._failure("native_patch_missing_end")

        files: list[PatchFile] = []
        current: PatchFile | None = None
        current_hunk: PatchHunk | None = None
        seen_paths: set[str] = set()

        for line in lines[1:-1]:
            if line.startswith("*** Add File: "):
                PatchParser._append_hunk(current, current_hunk)
                current_hunk = None

                path = line[len("*** Add File: "):].strip()
                if not path:
                    return PatchParser._failure("native_patch_missing_path", line=line)
                if path in seen_paths:
                    return PatchParser._failure("native_patch_duplicate_file", path=path)
                seen_paths.add(path)

                current = PatchParser._new_file(
                    path=path,
                    old_path="/dev/null",
                    new_path=path,
                    action="create"
                )
                files.append(current)
                continue

            if line.startswith("*** Update File: "):
                PatchParser._append_hunk(current, current_hunk)
                current_hunk = None

                path = line[len("*** Update File: "):].strip()
                if not path:
                    return PatchParser._failure("native_patch_missing_path", line=line)
                if path in seen_paths:
                    return PatchParser._failure("native_patch_duplicate_file", path=path)
                seen_paths.add(path)

                current = PatchParser._new_file(
                    path=path,
                    old_path=path,
                    new_path=path,
                    action="modify"
                )
                files.append(current)
                continue

            if line.startswith("*** Delete File: "):
                PatchParser._append_hunk(current, current_hunk)
                current_hunk = None

                path = line[len("*** Delete File: "):].strip()
                if not path:
                    return PatchParser._failure("native_patch_missing_path", line=line)
                if path in seen_paths:
                    return PatchParser._failure("native_patch_duplicate_file", path=path)
                seen_paths.add(path)

                current = PatchParser._new_file(
                    path=path,
                    old_path=path,
                    new_path="/dev/null",
                    action="delete"
                )
                files.append(current)
                continue

            if line.startswith("*** Move to: "):
                if current is None:
                    return PatchParser._failure("native_patch_move_without_file", line=line)

                path = line[len("*** Move to: "):].strip()
                if not path:
                    return PatchParser._failure("native_patch_missing_path", line=line)
                if path in seen_paths:
                    return PatchParser._failure("native_patch_duplicate_file", path=path)
                seen_paths.add(path)

                current = PatchParser._renamed_file(current, path)
                files[-1] = current
                continue

            if line.startswith("*** "):
                return PatchParser._failure("native_patch_unexpected_control_line", line=line)

            if current is None:
                if not line.strip():
                    continue
                return PatchParser._failure("native_patch_content_without_file", line=line)

            if line.startswith("@@"):
                PatchParser._append_hunk(current, current_hunk)
                current_hunk = PatchParser._new_hunk(line)
                continue

            if line == r"\ No newline at end of file":
                if current_hunk is None or not current_hunk.entries:
                    return PatchParser._failure("native_patch_no_newline_without_line", line=line)
                current_hunk.entries[-1].no_newline = True
                continue

            if not line:
                return PatchParser._failure("native_patch_bad_line", line=line)

            marker: PatchMarker
            if line[0] == " ":
                marker = " "
            elif line[0] == "+":
                marker = "+"
            elif line[0] == "-":
                marker = "-"
            else:
                return PatchParser._failure("native_patch_bad_line", line=line)

            if current.action == "create" and marker != "+":
                return PatchParser._failure(
                    "native_patch_bad_create_line",
                    path=current.path,
                    line=line
                )

            if current_hunk is None:
                current_hunk = PatchParser._new_hunk()
            if marker in {" ", "-"}:
                current_hunk.old_count += 1
            if marker in {" ", "+"}:
                current_hunk.new_count += 1
            current_hunk.declared_old_count = current_hunk.old_count
            current_hunk.declared_new_count = current_hunk.new_count

            current_hunk.entries.append(PatchLine(marker=marker, text=line[1:]))

        PatchParser._append_hunk(current, current_hunk)

        if not files:
            return PatchParser._failure("native_patch_no_files")
        for item in files:
            if item.action == "delete":
                continue
            if not item.hunks:
                return PatchParser._failure("native_patch_no_hunks", path=item.path)

        success: PatchParseSuccess = {"ok": True, "files": files}
        return success


if __name__ == '__main__':
    pass
