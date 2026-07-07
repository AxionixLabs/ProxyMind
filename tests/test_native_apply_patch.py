# -*- coding: utf-8 -*-

from pathlib import Path

from mind_app.native_coding import NativeCoding


def apply_patch_text(*lines: str) -> str:
    """生成严格 apply_patch 文本。"""
    return "\n".join(["*** Begin Patch", *lines, "*** End Patch"])


def single_delta_change(result: dict) -> dict:
    """返回 apply_patch 结果中的唯一 delta change。"""
    delta = result["data"]["delta"]
    assert delta["exact"] is True
    assert len(delta["changes"]) == 1
    return delta["changes"][0]


def test_missing_begin_returns_reason(tmp_path: Path) -> None:
    """缺少起始标记时返回稳定失败原因。"""
    patch = "*** Add File: demo.txt\n+hello\n*** End Patch"

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is False
    assert result["data"]["reason"] == "native_patch_missing_begin"


def test_add_file_patch_writes_file(tmp_path: Path) -> None:
    """合法新增文件补丁会写入工作区。"""
    patch = apply_patch_text(
        "*** Add File: demo.txt",
        "+hello",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)
    target = tmp_path / "demo.txt"

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert result["data"]["file_count"] == 1
    assert result["data"]["created_files"][0]["path"] == "demo.txt"
    change = single_delta_change(result)
    assert change["action"] == "create"
    assert change["path"] == "demo.txt"
    assert change["old_content"] is None
    assert change["new_content"] == "hello\n"


def test_add_file_patch_creates_nested_parent(tmp_path: Path) -> None:
    """新增文件补丁会自动创建父目录。"""
    patch = apply_patch_text(
        "*** Add File: nested/demo.txt",
        "+hello",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is True
    assert (tmp_path / "nested" / "demo.txt").read_text(encoding="utf-8") == "hello\n"
    assert result["data"]["created_files"][0]["path"] == "nested/demo.txt"


def test_add_file_patch_preserves_missing_final_newline(tmp_path: Path) -> None:
    """新增文件补丁支持无结尾换行标记。"""
    patch = apply_patch_text(
        "*** Add File: no_newline.txt",
        "+hello",
        r"\ No newline at end of file",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is True
    assert (tmp_path / "no_newline.txt").read_text(encoding="utf-8") == "hello"


def test_update_file_patch_modifies_existing_text(tmp_path: Path) -> None:
    """更新补丁会按上下文修改已有文件。"""
    target = tmp_path / "demo.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8", newline="")
    patch = apply_patch_text(
        "*** Update File: demo.txt",
        "@@",
        " alpha",
        "-beta",
        "+gamma",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"
    assert result["data"]["updated_files"][0]["path"] == "demo.txt"
    assert result["data"]["added_lines"] == 1
    assert result["data"]["removed_lines"] == 1
    change = single_delta_change(result)
    assert change["action"] == "modify"
    assert change["path"] == "demo.txt"
    assert change["old_content"] == "alpha\nbeta\n"
    assert change["new_content"] == "alpha\ngamma\n"


def test_update_file_patch_relocates_unique_context(tmp_path: Path) -> None:
    """更新补丁可以在唯一上下文移动后重新定位。"""
    target = tmp_path / "demo.txt"
    target.write_text("prefix\nalpha\nbeta\n", encoding="utf-8")
    patch = apply_patch_text(
        "*** Update File: demo.txt",
        "@@",
        " alpha",
        "-beta",
        "+gamma",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "prefix\nalpha\ngamma\n"
    assert result["data"]["relocated_hunk_count"] == 1


def test_delete_file_patch_removes_file(tmp_path: Path) -> None:
    """删除补丁会移除工作区内文件。"""
    target = tmp_path / "remove.txt"
    target.write_text("gone\n", encoding="utf-8", newline="")
    patch = apply_patch_text("*** Delete File: remove.txt")

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is True
    assert not target.exists()
    assert result["data"]["deleted_files"][0]["path"] == "remove.txt"
    change = single_delta_change(result)
    assert change["action"] == "delete"
    assert change["path"] == "remove.txt"
    assert change["old_content"] == "gone\n"
    assert change["new_content"] is None


def test_rename_file_patch_moves_and_updates_file(tmp_path: Path) -> None:
    """重命名补丁会移动源文件并写入更新内容。"""
    source = tmp_path / "old.txt"
    source.write_text("old\n", encoding="utf-8", newline="")
    patch = apply_patch_text(
        "*** Update File: old.txt",
        "*** Move to: new.txt",
        "@@",
        "-old",
        "+new",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is True
    assert not source.exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "new\n"
    assert result["data"]["changed_files"][0]["action"] == "rename"
    assert result["data"]["changed_files"][0]["source_path"] == "old.txt"
    change = single_delta_change(result)
    assert change["action"] == "rename"
    assert change["path"] == "new.txt"
    assert change["source_path"] == "old.txt"
    assert change["old_content"] == "old\n"
    assert change["new_content"] == "new\n"


def test_patch_rejects_path_outside_workspace(tmp_path: Path) -> None:
    """补丁路径不能越过工作区根目录。"""
    patch = apply_patch_text(
        "*** Add File: ../escape.txt",
        "+bad",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is False
    assert result["data"]["reason"] == "path_outside_workspace"
    assert not (tmp_path.parent / "escape.txt").exists()


def test_patch_rejects_duplicate_file_entries(tmp_path: Path) -> None:
    """同一个补丁不能重复声明相同文件。"""
    patch = apply_patch_text(
        "*** Add File: demo.txt",
        "+one",
        "*** Add File: demo.txt",
        "+two",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is False
    assert result["data"]["reason"] == "native_patch_duplicate_file"


def test_patch_rejects_existing_create_target(tmp_path: Path) -> None:
    """新增文件补丁不能覆盖已有文件。"""
    (tmp_path / "demo.txt").write_text("exists\n", encoding="utf-8")
    patch = apply_patch_text(
        "*** Add File: demo.txt",
        "+new",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is False
    assert result["data"]["reason"] == "file_already_exists"
    assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "exists\n"


def test_patch_reports_context_mismatch(tmp_path: Path) -> None:
    """上下文不匹配时返回稳定失败原因和诊断信息。"""
    (tmp_path / "demo.txt").write_text("alpha\n", encoding="utf-8")
    patch = apply_patch_text(
        "*** Update File: demo.txt",
        "@@",
        "-missing",
        "+beta",
    )

    result = NativeCoding(root=tmp_path).apply_patch(patch=patch)

    assert result["ok"] is False
    assert result["data"]["reason"] == "patch_context_mismatch"
    assert result["data"]["path"] == "demo.txt"
    assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "alpha\n"


def test_patch_expected_sha256_blocks_stale_write(tmp_path: Path) -> None:
    """SHA256 基线不匹配时阻止写入。"""
    target = tmp_path / "demo.txt"
    target.write_text("alpha\n", encoding="utf-8")
    patch = apply_patch_text(
        "*** Update File: demo.txt",
        "@@",
        "-alpha",
        "+beta",
    )

    result = NativeCoding(root=tmp_path).apply_patch(
        patch=patch,
        expected_sha256={"demo.txt": "0" * 64},
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "file_changed_since_read"
    assert target.read_text(encoding="utf-8") == "alpha\n"


def test_patch_force_bypasses_expected_sha256(tmp_path: Path) -> None:
    """force=True 时允许绕过 SHA256 基线冲突。"""
    target = tmp_path / "demo.txt"
    target.write_text("alpha\n", encoding="utf-8")
    patch = apply_patch_text(
        "*** Update File: demo.txt",
        "@@",
        "-alpha",
        "+beta",
    )

    result = NativeCoding(root=tmp_path).apply_patch(
        patch=patch,
        expected_sha256={"demo.txt": "0" * 64},
        force=True,
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "beta\n"
