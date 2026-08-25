# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
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
    preview_data: dict[str, typing.Any] | None = None,
    call_id: str = ""
) -> PatchView:
    """构建补丁开始执行时的结构化展示数据。"""
    raw_patch = str(arguments.get("patch") or "")
    if not isinstance(preview_data, dict):
        raise ValueError("apply_patch start requires a structured preview")
    if preview_data.get("ok", True) is False:
        raise ValueError("apply_patch start preview must be successful")

    return PatchView(
        call_id=_required_call_id(call_id),
        phase="proposed",
        raw_patch=raw_patch,
        files=_files_from_delta(preview_data),
        result_files=_result_files(preview_data),
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
    normalized: list[dict[str, typing.Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise TypeError("apply_patch result files must contain objects")
        file_summary: dict[str, typing.Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("apply_patch result file fields must use string keys")
            file_summary[key] = item
        normalized.append(file_summary)
    return tuple(normalized)


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

    if "hunks" not in change:
        raise ValueError("apply_patch delta change requires canonical hunks")
    hunks = _hunks_from_delta(change["hunks"])
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


def _hunks_from_delta(value: typing.Any) -> tuple[PatchHunkView, ...]:
    """读取原生 delta 已生成的 canonical hunk 行。"""
    if not isinstance(value, list):
        raise TypeError("apply_patch delta hunks must be a list")

    hunks: list[PatchHunkView] = []
    for hunk_index, raw_hunk in enumerate(value):
        if not isinstance(raw_hunk, dict):
            raise TypeError(f"apply_patch delta hunk {hunk_index} must be an object")
        raw_lines = raw_hunk.get("lines")
        if not isinstance(raw_lines, list):
            raise TypeError(f"apply_patch delta hunk {hunk_index} requires lines")

        lines: list[PatchLineView] = []
        for line_index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, dict):
                raise TypeError(
                    f"apply_patch delta hunk line {hunk_index}:{line_index} must be an object"
                )
            kind = str(raw_line.get("kind") or "").strip().lower()
            if kind not in {"context", "add", "remove"}:
                raise ValueError(f"unsupported apply_patch delta line kind: {kind!r}")
            text = raw_line.get("text")
            if not isinstance(text, str):
                raise TypeError("apply_patch delta hunk line text must be text")
            old_line = _line_number(raw_line.get("old_line"))
            new_line = _line_number(raw_line.get("new_line"))
            lines.append(PatchLineView(
                kind=kind,
                text=text,
                old_line=old_line,
                new_line=new_line,
            ))
        hunks.append(PatchHunkView(lines=tuple(lines)))
    return tuple(hunks)


def _line_number(value: typing.Any) -> int | None:
    """规范化 canonical hunk 中的可选行号。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("apply_patch delta line numbers must be positive integers")
    return value


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
            if number is not None:
                normalized.append(f"{str(number)}: {text}")
            else:
                normalized.append(text)
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
