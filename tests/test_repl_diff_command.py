# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path

from mind_app.client_tools.coding.native import coding_tools
from mind_app.client_tools.types import ClientToolRuntime
from mind_app.modes.support.repl_diff import (
    diff_stat,
    truncate_diff_text
)
from mind_app.native_coding import NativeCoding


def apply_patch_text(*lines: str) -> str:
    """生成严格 apply_patch 文本。"""
    return "\n".join(["*** Begin Patch", *lines, "*** End Patch"]) + "\n"


def run_async(value: object) -> object:
    """同步测试中运行异步逻辑。"""
    return asyncio.run(value)


def test_apply_patch_tool_tracks_current_diff(tmp_path: Path) -> None:
    """apply_patch 工具成功后记录当前净差异。"""
    target = tmp_path / "demo.txt"
    target.write_text("old\n", encoding="utf-8")

    coding = NativeCoding(root=tmp_path)
    tool = next(item for item in coding_tools(coding) if item.name == "apply_patch")

    patch = apply_patch_text(
        "*** Update File: demo.txt",
        "@@",
        "-old",
        "+new"
    )

    result = run_async(tool.handler(
        {"patch": patch},
        ClientToolRuntime(session=None)
    ))

    assert result.isError is False

    snapshot = coding.patch_diff_snapshot()
    diff = snapshot["diff"]
    assert snapshot["invalidated"] is False
    assert "diff --git a/demo.txt b/demo.txt" in diff
    assert "-old" in diff
    assert "+new" in diff


def test_truncate_diff_text_limits_lines() -> None:
    """diff 展示会按固定行数截断。"""
    text = "".join(f"+line {index}\n" for index in range(5))

    rendered, truncated = truncate_diff_text(text, max_lines=3, max_chars=1000)

    assert rendered == "+line 0\n+line 1\n+line 2"
    assert truncated is True


def test_diff_stat_ignores_diff_headers() -> None:
    """diff 统计不把文件头当作增删行。"""
    files, added, removed = diff_stat(
        "diff --git a/demo.txt b/demo.txt\n"
        "--- a/demo.txt\n"
        "+++ b/demo.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    assert (files, added, removed) == (1, 1, 1)
