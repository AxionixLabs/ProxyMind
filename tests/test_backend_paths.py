# -*- coding: utf-8 -*-

import sys

from backend.utilities.paths import resource_path


def test_backend_resource_path_prefers_backend_web_in_source_tree() -> None:
    """源码模式下 backend 静态页面不串到 Mind 控制面页面。"""
    index = resource_path("web", "index.html")

    assert index.as_posix().endswith("/backend/web/index.html")
    assert "HELIX SERVER" in index.read_text(encoding="utf-8", errors="replace")


def test_backend_resource_path_prefers_direct_web_for_packaged_entry(
    tmp_path,
    monkeypatch
) -> None:
    """打包入口下 backend 资源读取分发根目录。"""
    direct = tmp_path / "web" / "index.html"
    nested = tmp_path / "backend" / "web" / "index.html"
    direct.parent.mkdir(parents=True)
    nested.parent.mkdir(parents=True)
    direct.write_text("packaged helix", encoding="utf-8")
    nested.write_text("source helix", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(tmp_path / "helix")])
    monkeypatch.chdir(tmp_path)

    assert resource_path("web", "index.html") == direct
