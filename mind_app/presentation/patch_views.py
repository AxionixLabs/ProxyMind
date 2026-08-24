# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import difflib
from pathlib import Path
from .models import (
    PatchAction,
    PatchDiagnosticView,
    PatchFileView,
    PatchHunkView,
    PatchLineView,
    PatchView
)


def build_patch_start_view(
    arguments: dict[str, typing.Any],
    *,
    call_id: str = ""
) -> PatchView:
    """构建补丁开始执行时的结构化展示数据。"""
    raw_patch = str(arguments.get("patch") or "")
    return PatchView(
        call_id=_required_call_id(call_id),
        phase="applying",
        raw_patch=raw_patch,
    )


def build_patch_result_view(
    arguments: dict[str, typing.Any],
    *,
    ok: bool,
    data: typing.Any = None,
    cost_ms: int | None = None,
    call_id: str = ""
) -> PatchView:
    """构建补丁执行完成后的结构化展示数据。"""
    raw_patch = str(arguments.get("patch") or "")
    payload   = dict(data) if isinstance(data, dict) else {}
    files     = _files_from_delta(payload) if ok else ()

    return PatchView(
        call_id=_required_call_id(call_id),
        phase="applied" if ok else "failed",
        raw_patch=raw_patch,
        files=files,
        result_files=_result_files(payload) if ok else (),
        diagnostics=() if ok else _diagnostics_from_payload(payload),
        cost_ms=_normalized_cost_ms(cost_ms),
    )


def _result_files(payload: dict[str, typing.Any]) -> tuple[dict[str, typing.Any], ...]:
    """读取 apply_patch 当前结果契约中的原始文件摘要。"""
    values = payload.get("files")
    if not isinstance(values, list):
        raise ValueError("successful apply_patch result requires data.files")
    if any(not isinstance(value, dict) for value in values):
        raise TypeError("apply_patch result files must contain objects")
    return tuple(dict(value) for value in values)


def _files_from_delta(payload: dict[str, typing.Any]) -> tuple[PatchFileView, ...]:
    """从已应用的精确内容变化构造文件差异。"""
    delta = payload.get("delta")
    if not isinstance(delta, dict):
        raise ValueError("successful apply_patch result requires data.delta")
    changes = delta.get("changes")
    if not isinstance(changes, list):
        raise ValueError("successful apply_patch result requires data.delta.changes")

    files: list[PatchFileView] = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise TypeError(f"apply_patch delta change {index} must be an object")
        files.append(_file_from_delta_change(change))
    return tuple(files)


def _file_from_delta_change(change: dict[str, typing.Any]) -> PatchFileView:
    """把单项已提交内容变化转换为结构化文件差异。"""
    action      = _patch_action(change.get("action"))
    path        = _display_path(change.get("path"))
    source_path = _display_path(change.get("source_path"))

    if not path:
        raise ValueError("apply_patch delta change requires path")
    if action == "rename" and not source_path:
        raise ValueError("apply_patch rename delta requires source_path")

    old_path = source_path if action == "rename" else path
    new_path = path

    old_content = change.get("old_content")
    new_content = change.get("new_content")

    if old_content is not None and not isinstance(old_content, str):
        raise TypeError("apply_patch delta old_content must be text or null")
    if new_content is not None and not isinstance(new_content, str):
        raise TypeError("apply_patch delta new_content must be text or null")

    if action == "add" and new_content is None:
        raise ValueError("apply_patch add delta requires new_content")
    if action == "delete" and old_content is None:
        raise ValueError("apply_patch delete delta requires old_content")
    if action in {"update", "rename"} and (
        old_content is None or new_content is None
    ):
        raise ValueError(f"apply_patch {action} delta requires old_content and new_content")

    if action == "add":
        hunks = (_content_hunk(str(new_content or ""), kind="add"),)
    elif action == "delete":
        hunks = (_content_hunk(str(old_content or ""), kind="remove"),)
    else:
        hunks = _updated_content_hunks(
            old_content=old_content,
            new_content=new_content,
        )

    hunks = tuple(hunk for hunk in hunks if hunk.lines)

    added, removed = _line_totals(hunks)

    return PatchFileView(
        action=action,
        old_path=old_path,
        new_path=new_path,
        hunks=hunks,
        added=added,
        removed=removed,
        old_line_count=len(str(old_content or "").splitlines()),
        new_line_count=len(str(new_content or "").splitlines()),
    )


def _updated_content_hunks(
    *,
    old_content: str | None,
    new_content: str | None
) -> tuple[PatchHunkView, ...]:
    """按 Codex 使用的三行上下文生成结构化差异块。"""
    old_lines = str(old_content or "").splitlines()
    new_lines = str(new_content or "").splitlines()

    matcher = difflib.SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    )

    return tuple(
        PatchHunkView(lines=_lines_from_opcodes(group, old_lines, new_lines))
        for group in matcher.get_grouped_opcodes(n=3)
    )


def _lines_from_opcodes(
    opcodes: typing.Iterable[tuple[str, int, int, int, int]],
    old_lines: list[str],
    new_lines: list[str],
) -> tuple[PatchLineView, ...]:
    """把一组差异操作转换为带双侧行号的展示行。"""
    lines: list[PatchLineView] = []
    for tag, old_start, old_end, new_start, new_end in opcodes:
        if tag == "equal":
            lines.extend(
                PatchLineView(
                    kind="context",
                    text=old_lines[old_index],
                    old_line=old_index + 1,
                    new_line=new_index + 1,
                )
                for old_index, new_index in zip(
                    range(old_start, old_end),
                    range(new_start, new_end),
                    strict=True,
                )
            )
            continue
        if tag in {"delete", "replace"}:
            lines.extend(
                PatchLineView(
                    kind="remove",
                    text=old_lines[index],
                    old_line=index + 1,
                )
                for index in range(old_start, old_end)
            )
        if tag in {"insert", "replace"}:
            lines.extend(
                PatchLineView(
                    kind="add",
                    text=new_lines[index],
                    new_line=index + 1,
                )
                for index in range(new_start, new_end)
            )
    return tuple(lines)


def _content_hunk(
    content: str,
    *,
    kind: typing.Literal["add", "remove"]
) -> PatchHunkView:
    """把新增或删除文件的完整内容转换为差异行。"""
    lines = tuple(
        PatchLineView(
            kind=kind,
            text=text,
            old_line=index if kind == "remove" else None,
            new_line=index if kind == "add" else None,
        )
        for index, text in enumerate(str(content).splitlines(), start=1)
    )
    return PatchHunkView(lines=lines)


def _line_totals(hunks: tuple[PatchHunkView, ...]) -> tuple[int, int]:
    """统计结构化 hunk 的新增与删除行数。"""
    added   = sum(line.kind == "add" for hunk in hunks for line in hunk.lines)
    removed = sum(line.kind == "remove" for hunk in hunks for line in hunk.lines)

    return added, removed


def _patch_action(value: typing.Any) -> PatchAction:
    """把执行层动作名称转换为展示层动作名称。"""
    normalized = str(value or "").strip().lower()

    actions: dict[str, PatchAction] = {
        "create": "add",
        "delete": "delete",
        "modify": "update",
        "rename": "rename",
    }

    try:
        return actions[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported apply_patch delta action: {normalized!r}") from error


def _display_path(value: typing.Any) -> str:
    """规范化补丁路径并尽量转换为当前目录相对路径。"""
    text = str(value or "").strip().replace("\\", "/")
    if not text or text == "/dev/null":
        return ""
    path = Path(text)
    if path.is_absolute():
        try:
            return path.relative_to(Path.cwd()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def _required_call_id(value: typing.Any) -> str:
    """返回 patch cell 必需的稳定调用身份。"""
    call_id = str(value or "").strip()
    if not call_id:
        raise ValueError("apply_patch presentation requires call_id")
    return call_id


def _diagnostics_from_payload(payload: dict[str, typing.Any]) -> tuple[PatchDiagnosticView, ...]:
    """按稳定顺序提取补丁失败的有价值诊断字段。"""
    definitions = (
        ("reason", payload.get("reason")),
        ("error", payload.get("error")),
        ("file", payload.get("path") or payload.get("source_path")),
        ("hunk", payload.get("hunk_header") or payload.get("header") or payload.get("hunk")),
        ("line", payload.get("target_line") or payload.get("line")),
        ("expected", payload.get("expected_sequence") or payload.get("expected")),
        ("actual", payload.get("actual_sequence") or payload.get("actual")),
        ("nearby", payload.get("nearby")),
    )
    diagnostics: list[PatchDiagnosticView] = []
    for label, value in definitions:
        values = _diagnostic_values(value, nearby=label == "nearby")
        if values:
            diagnostics.append(PatchDiagnosticView(label=label, values=values))
    if diagnostics:
        return tuple(diagnostics)
    return (PatchDiagnosticView(label="error", values=("patch failed",)),)


def _diagnostic_values(
    value: typing.Any,
    *,
    nearby: bool
) -> tuple[str, ...]:
    """把标量或序列诊断值规范化为可展示文本。"""
    if value is None or value == "":
        return ()
    values = value if isinstance(value, (list, tuple)) else (value,)
    normalized: list[str] = []
    for item in values:
        if nearby and isinstance(item, dict):
            number = item.get("line")
            text = str(item.get("text") or "")
            normalized.append(f"{number}: {text}" if number is not None else text)
        else:
            normalized.append(str(item))
    return tuple(text for text in normalized if text)


def _normalized_cost_ms(value: int | None) -> int | None:
    """规范化可选的非负毫秒耗时。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


if __name__ == '__main__':
    pass
