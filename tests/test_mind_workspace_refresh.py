# -*- coding: utf-8 -*-

from pathlib import Path

from mind_app.history import normalize_workspace
from mind_app.mind_core import Mind


def test_set_history_workspace_keeps_native_coding_when_workspace_unchanged(tmp_path: Path) -> None:
    """workspace 未变化时不重建 native coding，避免清空运行会话。"""
    mind = Mind.__new__(Mind)
    mind.history_workspace = normalize_workspace(tmp_path)
    mind.native_coding = object()
    mind.client_tools = object()

    original_native = mind.native_coding
    original_tools = mind.client_tools

    result = Mind.set_history_workspace(mind, tmp_path)

    assert result == normalize_workspace(tmp_path)
    assert mind.native_coding is original_native
    assert mind.client_tools is original_tools
