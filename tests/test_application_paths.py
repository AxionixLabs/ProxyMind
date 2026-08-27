# -*- coding: utf-8 -*-

import pytest

from mind_core.application_paths import (
    is_packaged_executable,
    resolve_application_layout,
)


def test_packaged_executable_is_judged_by_path_name(tmp_path) -> None:
    assert is_packaged_executable(tmp_path / "mind") is True
    assert is_packaged_executable(tmp_path / "MIND.EXE") is True
    assert is_packaged_executable(tmp_path / "python") is False


def test_source_layout_uses_mind_py_directory(tmp_path) -> None:
    entry = tmp_path / "mind.py"

    layout = resolve_application_layout(
        entry_file=entry,
        argv0=entry,
        executable=tmp_path / "venv" / "python.exe",
        platform="win32",
    )

    assert layout.mode == "source"
    assert layout.packaged is False
    assert layout.executable == entry.resolve()
    assert layout.root == tmp_path.resolve()
    assert layout.supports == (
        tmp_path / "schematic" / "supports" / "windows"
    ).resolve()


def test_windows_packaged_layout_uses_executable_directory(tmp_path) -> None:
    executable = tmp_path / "applications" / "MindEngine" / "mind.exe"

    layout = resolve_application_layout(
        argv0="mind.exe",
        executable=executable,
        platform="win32",
    )

    assert layout.mode == "packaged"
    assert layout.packaged is True
    assert layout.root == executable.parent.resolve()
    assert layout.supports == (
        executable.parent / "schematic" / "supports" / "windows"
    ).resolve()


def test_macos_packaged_layout_uses_bundle_macos_directory(tmp_path) -> None:
    executable = (
        tmp_path
        / "applications"
        / "Mind.app"
        / "Contents"
        / "MacOS"
        / "mind"
    )

    layout = resolve_application_layout(
        argv0="Mind",
        executable=executable,
        platform="darwin",
    )

    assert layout.mode == "packaged"
    assert layout.root == executable.parent.resolve()
    assert layout.supports == (
        executable.parent / "schematic" / "supports" / "macos"
    ).resolve()


def test_packaged_layout_falls_back_to_absolute_argv_path(tmp_path) -> None:
    executable = tmp_path / "MindEngine" / "mind.exe"

    layout = resolve_application_layout(
        argv0=executable,
        executable=tmp_path / "venv" / "python.exe",
        platform="win32",
    )

    assert layout.executable == executable.resolve()
    assert layout.root == executable.parent.resolve()


def test_application_layout_rejects_unknown_entry(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported entry"):
        resolve_application_layout(
            argv0=tmp_path / "pytest.exe",
            platform="win32",
        )
