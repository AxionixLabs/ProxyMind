# -*- coding: utf-8 -*-

import pytest

from mind_app.client_tools.coding.native import coding_tools
from mind_app.native_coding import NativeCoding
from mind_app.native_coding.edit.parser import PatchParser
from mind_app.stream_events.tool_traces.native_patch import _patch_preview_lines


def test_strict_patch_create_remains_supported(tmp_path) -> None:
    coding = NativeCoding(root=tmp_path)
    patch = (
        "*** Begin Patch\n"
        "*** Add File: strict.txt\n"
        "+strict\n"
        "*** End Patch\n"
    )

    result = coding.apply_patch(patch=patch)

    assert result["ok"]
    assert (tmp_path / "strict.txt").read_text(encoding="utf-8") == "strict\n"
    assert result["data"]["created_files"][0]["path"] == "strict.txt"


def test_strict_patch_modify_and_delete_remain_supported(tmp_path) -> None:
    (tmp_path / "keep.txt").write_text("old\n", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("gone\n", encoding="utf-8")
    coding = NativeCoding(root=tmp_path)
    patch = (
        "*** Begin Patch\n"
        "*** Update File: keep.txt\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** Delete File: gone.txt\n"
        "*** End Patch\n"
    )

    result = coding.apply_patch(patch=patch)

    assert result["ok"]
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "new\n"
    assert not (tmp_path / "gone.txt").exists()


def test_strict_patch_rename_preserves_missing_final_newline(tmp_path) -> None:
    (tmp_path / "source.txt").write_bytes(b"old")
    coding = NativeCoding(root=tmp_path)
    patch = (
        "*** Begin Patch\n"
        "*** Update File: source.txt\n"
        "*** Move to: renamed.txt\n"
        "@@\n"
        "-old\n"
        "\\ No newline at end of file\n"
        "+new\n"
        "\\ No newline at end of file\n"
        "*** End Patch\n"
    )

    result = coding.apply_patch(patch=patch)

    assert result["ok"]
    assert not (tmp_path / "source.txt").exists()
    assert (tmp_path / "renamed.txt").read_bytes() == b"new"


def test_git_diff_creates_file_and_preserves_missing_final_newline(tmp_path) -> None:
    coding = NativeCoding(root=tmp_path)
    patch = (
        "diff --git a/nested/hello.txt b/nested/hello.txt\n"
        "new file mode 100644\n"
        "index 0000000..1234567\n"
        "--- /dev/null\n"
        "+++ b/nested/hello.txt\n"
        "@@ -0,0 +1,2 @@\n"
        "+hello\n"
        "+world\n"
        "\\ No newline at end of file\n"
    )

    result = coding.apply_patch(patch=patch)

    assert result["ok"]
    assert (tmp_path / "nested" / "hello.txt").read_bytes() == b"hello\nworld"
    assert result["data"]["added_lines"] == 2


def test_plain_unified_diff_modifies_and_deletes_multiple_files(tmp_path) -> None:
    (tmp_path / "sample.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("remove\n", encoding="utf-8")
    coding = NativeCoding(root=tmp_path)
    patch = (
        "--- sample.txt\n"
        "+++ sample.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " alpha\n"
        "-beta\n"
        "+BETA\n"
        " gamma\n"
        "--- gone.txt\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-remove\n"
    )

    result = coding.apply_patch(patch=patch)

    assert result["ok"]
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"
    assert not (tmp_path / "gone.txt").exists()
    assert result["data"]["file_count"] == 2
    assert result["data"]["updated_files"][0]["path"] == "sample.txt"
    assert result["data"]["deleted_files"][0]["path"] == "gone.txt"


def test_git_diff_uses_declared_hunk_position(tmp_path) -> None:
    target = tmp_path / "repeated.txt"
    target.write_text("one\ntarget\none\ntarget\n", encoding="utf-8")
    coding = NativeCoding(root=tmp_path)
    patch = (
        "diff --git a/repeated.txt b/repeated.txt\n"
        "index 1234567..7654321 100644\n"
        "--- a/repeated.txt\n"
        "+++ b/repeated.txt\n"
        "@@ -3,2 +3,2 @@\n"
        " one\n"
        "-target\n"
        "+updated\n"
    )

    result = coding.apply_patch(patch=patch)

    assert result["ok"]
    assert target.read_text(encoding="utf-8") == "one\ntarget\none\nupdated\n"


def test_unified_diff_rejects_bad_hunk_counts() -> None:
    parsed = PatchParser.parse_patch(
        "--- sample.txt\n"
        "+++ sample.txt\n"
        "@@ -1,2 +1 @@\n"
        "-alpha\n"
        "+beta\n"
    )

    assert not parsed["ok"]
    assert parsed["reason"] == "native_patch_hunk_count_mismatch"
    assert parsed["data"]["actual_old_count"] == 1


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        (
            "diff --git a/file.bin b/file.bin\n"
            "Binary files a/file.bin and b/file.bin differ\n",
            "native_patch_unsupported_binary",
        ),
        (
            "diff --git a/file.txt b/file.txt\n"
            "old mode 100644\n"
            "new mode 100755\n",
            "native_patch_unsupported_metadata",
        ),
        (
            "diff --git \"a/file name.txt\" \"b/file name.txt\"\n",
            "native_patch_unsupported_quoted_path",
        ),
    ],
)
def test_git_diff_rejects_unsupported_forms(patch, reason) -> None:
    parsed = PatchParser.parse_patch(patch)

    assert not parsed["ok"]
    assert parsed["reason"] == reason


def test_unified_diff_keeps_workspace_path_guard(tmp_path) -> None:
    coding = NativeCoding(root=tmp_path)
    patch = (
        "--- /dev/null\n"
        "+++ ../outside.txt\n"
        "@@ -0,0 +1 @@\n"
        "+outside\n"
    )

    result = coding.apply_patch(patch=patch)

    assert not result["ok"]
    assert result["data"]["reason"] == "path_outside_workspace"
    assert not (tmp_path.parent / "outside.txt").exists()


@pytest.mark.parametrize(
    "patch",
    [
        (
            "*** Begin Patch\n"
            "*** Add File: strict.txt\n"
            "+strict\n"
            "*** End Patch\n"
        ),
        (
            "diff --git a/unified.txt b/unified.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/unified.txt\n"
            "@@ -0,0 +1 @@\n"
            "+unified\n"
        ),
    ],
)
def test_patch_preview_uses_both_supported_formats(patch) -> None:
    preview = "\n".join(_patch_preview_lines(patch))

    assert "(+1 -0)" in preview
    assert "+strict" in preview or "+unified" in preview


def test_apply_patch_tool_exposes_patch_shape_and_formats(tmp_path) -> None:
    coding = NativeCoding(root=tmp_path)
    tool = next(item for item in coding_tools(coding) if item.name == "apply_patch")
    wire = tool.to_mcp_tool()
    patch_schema = wire.inputSchema["properties"]["patch"]

    assert wire.inputSchema["required"] == ["patch"]
    assert "unified diff" in patch_schema["description"]
    assert "git diff" in patch_schema["description"]
    assert '{"patch": "补丁文本"}' in tool.description
