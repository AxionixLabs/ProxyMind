# -*- coding: utf-8 -*-

import sys

import pytest

from infrastructure.platform import macos_security


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS xattrs")
def test_macos_quarantine_round_trip(tmp_path) -> None:
    target = tmp_path / "tool"
    target.write_bytes(b"binary")
    macos_security._run_xattr((
        "-w",
        macos_security.MACOS_QUARANTINE_ATTRIBUTE,
        "0081;00000000;tests;00000000-0000-0000-0000-000000000000",
        str(target),
    ))

    assert macos_security.macos_path_has_quarantine(target)
    macos_security.remove_macos_quarantine_tree(tmp_path)
    assert not macos_security.macos_path_has_quarantine(target)


def test_macos_path_has_quarantine_reports_present(
    monkeypatch,
    tmp_path,
) -> None:
    target = tmp_path / "tool"
    target.write_bytes(b"binary")

    monkeypatch.setattr(
        macos_security,
        "_run_xattr",
        lambda _arguments: "com.apple.quarantine\n",
    )

    assert macos_security.macos_path_has_quarantine(target)


def test_macos_path_has_quarantine_reports_clean(
    monkeypatch,
    tmp_path,
) -> None:
    target = tmp_path / "tool"
    target.write_bytes(b"binary")

    monkeypatch.setattr(
        macos_security,
        "_run_xattr",
        lambda _arguments: "com.apple.provenance\n",
    )

    assert not macos_security.macos_path_has_quarantine(target)


def test_remove_macos_quarantine_tree_uses_recursive_delete(
    monkeypatch,
    tmp_path,
) -> None:
    commands = []

    def run_xattr(arguments) -> str:
        commands.append(arguments)
        return ""

    monkeypatch.setattr(
        macos_security,
        "_run_xattr",
        run_xattr,
    )

    macos_security.remove_macos_quarantine_tree(tmp_path)

    assert commands == [(
        "-dr",
        macos_security.MACOS_QUARANTINE_ATTRIBUTE,
        str(tmp_path),
    )]


def test_remove_macos_quarantine_tree_propagates_unexpected_error(
    monkeypatch,
    tmp_path,
) -> None:
    def permission_denied(_arguments):
        raise PermissionError("denied")

    monkeypatch.setattr(
        macos_security,
        "_run_xattr",
        permission_denied,
    )

    with pytest.raises(PermissionError):
        macos_security.remove_macos_quarantine_tree(tmp_path)
