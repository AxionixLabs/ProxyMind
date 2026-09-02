# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
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

_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)?$"
)

_ALLOWED_GIT_METADATA = (
    "index ",
    "new file mode ",
    "deleted file mode "
)

_UNSUPPORTED_GIT_METADATA = (
    "old mode ",
    "new mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to "
)


class _UnifiedPatchError(Exception):
    """保存 unified 补丁解析失败信息。"""

    def __init__(self, reason: str, **data: typing.Any) -> None:
        """记录稳定错误原因和相关字段。"""
        super().__init__(reason)
        self.reason = reason
        self.data = data


def _fail(reason: str, **data: typing.Any) -> typing.NoReturn:
    """中断当前解析流程并携带结构化失败信息。"""
    raise _UnifiedPatchError(reason, **data)


def _header_path(line: str, *, prefix: str, git_style: bool) -> str:
    """从文件头中提取并规范化相对路径。"""
    raw = line[len(prefix):]
    if "\t" in raw:
        raw = raw.split("\t", 1)[0]

    path = raw.strip()

    if not path:
        _fail("native_patch_missing_path", line=line)
    if path.startswith('"') or path.endswith('"'):
        _fail("native_patch_unsupported_quoted_path", path=path)
    if path == "/dev/null":
        return path

    if git_style and path.startswith(("a/", "b/")):
        path = path[2:]
    if not path:
        _fail("native_patch_missing_path", line=line)

    return path


def _file_action(
    old_path: str,
    new_path: str
) -> tuple[PatchAction, str]:
    """根据新旧文件路径确定补丁动作和目标路径。"""
    if old_path == "/dev/null" and new_path == "/dev/null":
        _fail("native_patch_bad_file_header", old_path=old_path, new_path=new_path)
    if old_path == "/dev/null":
        return "create", new_path
    if new_path == "/dev/null":
        return "delete", old_path
    if old_path != new_path:
        _fail(
            "native_patch_unsupported_rename",
            old_path=old_path,
            new_path=new_path
        )

    return "modify", new_path


def _parse_hunk(
    lines: list[str],
    index: int
) -> tuple[PatchHunk, int]:
    """解析一个带标准行号和计数的 unified hunk。"""
    header = lines[index]

    match = _HUNK_HEADER.fullmatch(header)
    if match is None:
        _fail("native_patch_bad_hunk_header", line=header)

    old_start = int(match.group(1))
    old_count = int(match.group(2) if match.group(2) is not None else 1)
    new_start = int(match.group(3))
    new_count = int(match.group(4) if match.group(4) is not None else 1)

    body: list[PatchLine] = []

    actual_old = 0
    actual_new = 0

    index += 1

    while index < len(lines):
        line = lines[index]

        if line == r"\ No newline at end of file":
            if not body:
                _fail("native_patch_no_newline_without_line", line=line)
            body[-1].no_newline = True
            index += 1
            continue

        if actual_old == old_count and actual_new == new_count:
            break
        if not line or line[0] not in {" ", "+", "-"}:
            _fail(
                "native_patch_hunk_count_mismatch",
                header=header,
                declared_old_count=old_count,
                declared_new_count=new_count,
                actual_old_count=actual_old,
                actual_new_count=actual_new
            )

        marker: PatchMarker
        if line[0] == " ":
            marker = " "
        elif line[0] == "+":
            marker = "+"
        else:
            marker = "-"
        if marker in {" ", "-"}:
            actual_old += 1
        if marker in {" ", "+"}:
            actual_new += 1
        if actual_old > old_count or actual_new > new_count:
            _fail(
                "native_patch_hunk_count_mismatch",
                header=header,
                declared_old_count=old_count,
                declared_new_count=new_count,
                actual_old_count=actual_old,
                actual_new_count=actual_new
            )

        body.append(PatchLine(marker=marker, text=line[1:]))
        index += 1

    if actual_old != old_count or actual_new != new_count:
        _fail(
            "native_patch_hunk_count_mismatch",
            header=header,
            declared_old_count=old_count,
            declared_new_count=new_count,
            actual_old_count=actual_old,
            actual_new_count=actual_new
        )
    if not body:
        _fail("native_patch_no_hunks", header=header)

    hunk = PatchHunk(
        header=header,
        old_start=old_start,
        new_start=new_start,
        old_count=actual_old,
        new_count=actual_new,
        declared_old_count=old_count,
        declared_new_count=new_count,
        has_declared_position=True,
        entries=body
    )

    return hunk, index


def _validate_action_hunks(item: PatchFile) -> None:
    """校验新建和删除补丁的 hunk 方向及连续位置。"""
    action = item.action
    if action not in {"create", "delete"}:
        return

    expected_line = 1
    for hunk in item.hunks:
        if action == "create":
            valid_header = hunk.old_start == 0 and hunk.old_count == 0
            valid_lines = all(line.marker == "+" for line in hunk.entries)
            start = hunk.new_start
            count = hunk.new_count
        else:
            valid_header = hunk.new_start == 0 and hunk.new_count == 0
            valid_lines = all(line.marker == "-" for line in hunk.entries)
            start = hunk.old_start
            count = hunk.old_count

        if not valid_header or not valid_lines or start != expected_line:
            _fail(
                "native_patch_invalid_file_hunk",
                path=item.path,
                action=action,
                header=hunk.header
            )
        expected_line += count


def _parse_unified_files(patch: str) -> list[PatchFile]:
    """解析 unified diff 或 git diff 并返回文件结构。"""
    lines = str(patch or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    files: list[PatchFile] = []
    seen_paths: set[str] = set()
    index: int = 0
    git_style: bool = False
    pending_git_section: bool = False
    action_hint: str = ""

    while index < len(lines):
        line = lines[index]

        if not line:
            index += 1
            continue

        if line.startswith("diff --git "):
            if pending_git_section:
                _fail("native_patch_unsupported_metadata", line=line)
            if '"' in line[len("diff --git "):]:
                _fail("native_patch_unsupported_quoted_path", line=line)
            git_style = True
            pending_git_section = True
            action_hint = ""
            index += 1
            continue

        if line.startswith(("GIT binary patch", "Binary files ")):
            _fail("native_patch_unsupported_binary", line=line)

        if line.startswith(_UNSUPPORTED_GIT_METADATA):
            _fail("native_patch_unsupported_metadata", line=line)

        if line.startswith(_ALLOWED_GIT_METADATA):
            if not pending_git_section:
                _fail("native_patch_unexpected_metadata", line=line)
            if line.startswith("new file mode "):
                action_hint = "create"
            elif line.startswith("deleted file mode "):
                action_hint = "delete"
            index += 1
            continue

        if not line.startswith("--- "):
            _fail("native_patch_unexpected_line", line=line)
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            _fail("native_patch_missing_new_file_header", line=line)

        old_path = _header_path(line, prefix="--- ", git_style=git_style)
        new_path = _header_path(lines[index + 1], prefix="+++ ", git_style=git_style)

        action, path = _file_action(old_path, new_path)
        if action_hint and action_hint != action:
            _fail(
                "native_patch_bad_file_header",
                path=path,
                expected_action=action_hint,
                action=action
            )
        if path in seen_paths:
            _fail("native_patch_duplicate_file", path=path)
        seen_paths.add(path)

        item = PatchFile(
            path=path,
            old_path=old_path,
            new_path=new_path,
            action=action
        )
        index += 2

        while index < len(lines) and lines[index].startswith("@@"):
            hunk, index = _parse_hunk(lines, index)
            item.hunks.append(hunk)

        if not item.hunks:
            _fail("native_patch_no_hunks", path=path)
        _validate_action_hunks(item)

        files.append(item)
        pending_git_section = False
        action_hint = ""

    if pending_git_section:
        _fail("native_patch_unsupported_metadata", line="diff --git")
    if not files:
        _fail("native_patch_no_files")

    return files


def parse_unified_patch(patch: str) -> PatchParseResult:
    """解析 unified diff 或 git diff 文本。"""
    try:
        files = _parse_unified_files(patch)
    except _UnifiedPatchError as exc:
        failure: PatchParseFailure = {
            "ok": False,
            "reason": exc.reason,
            "data": exc.data
        }
        return failure

    success: PatchParseSuccess = {"ok": True, "files": files}
    return success


if __name__ == '__main__':
    pass
