# -*- coding: utf-8 -*-

from mind_app.native_coding.edit.turn_diff import TurnDiffTracker


def exact_delta(*changes: dict) -> dict:
    """生成精确 apply_patch delta 载荷。"""
    return {"exact": True, "changes": list(changes)}


def change(
    action: str,
    path: str,
    *,
    old: str | None = None,
    new: str | None = None,
    source: str | None = None
) -> dict:
    """生成单个 apply_patch change 载荷。"""
    return {
        "action": action,
        "path": path,
        "old_content": old,
        "new_content": new,
        "source_path": source,
        "overwritten_content": None,
    }


def test_turn_diff_tracks_created_file() -> None:
    """新增文件 delta 会生成 new file diff。"""
    tracker = TurnDiffTracker()

    diff = tracker.track_delta(exact_delta(
        change("create", "demo.txt", new="hello\n")
    ))

    assert "diff --git a/demo.txt b/demo.txt" in diff
    assert "new file mode 100644" in diff
    assert "--- /dev/null" in diff
    assert "+++ b/demo.txt" in diff
    assert "+hello" in diff


def test_turn_diff_merges_multiple_updates_to_net_diff() -> None:
    """同一文件多次更新会合并为本轮净 diff。"""
    tracker = TurnDiffTracker()

    tracker.track_delta(exact_delta(
        change("modify", "demo.txt", old="alpha\nbeta\n", new="alpha\ngamma\n")
    ))
    diff = tracker.track_delta(exact_delta(
        change("modify", "demo.txt", old="alpha\ngamma\n", new="alpha\ndelta\n")
    ))

    assert "-beta" in diff
    assert "+delta" in diff
    assert "gamma" not in diff
    assert diff.count("diff --git") == 1


def test_turn_diff_drops_create_then_delete() -> None:
    """同一轮新增后删除不会留下净 diff。"""
    tracker = TurnDiffTracker()

    tracker.track_delta(exact_delta(
        change("create", "temp.txt", new="temporary\n")
    ))
    diff = tracker.track_delta(exact_delta(
        change("delete", "temp.txt", old="temporary\n")
    ))

    assert diff == ""


def test_turn_diff_tracks_rename_with_content_change() -> None:
    """重命名 delta 会保留源路径和目标路径。"""
    tracker = TurnDiffTracker()

    diff = tracker.track_delta(exact_delta(
        change("rename", "new.txt", source="old.txt", old="old\n", new="new\n")
    ))

    assert "diff --git a/old.txt b/new.txt" in diff
    assert "rename from old.txt" in diff
    assert "rename to new.txt" in diff
    assert "--- a/old.txt" in diff
    assert "+++ b/new.txt" in diff
    assert "-old" in diff
    assert "+new" in diff


def test_turn_diff_invalidates_on_inexact_delta() -> None:
    """非精确 delta 会让 tracker 失效并清空 diff。"""
    tracker = TurnDiffTracker()

    tracker.track_delta(exact_delta(
        change("create", "demo.txt", new="hello\n")
    ))
    diff = tracker.track_delta({"exact": False, "changes": []})

    assert diff == ""
    assert tracker.invalidated is True
    assert tracker.unified_diff == ""
