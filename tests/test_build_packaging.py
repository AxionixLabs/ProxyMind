# -*- coding: utf-8 -*-

from unittest.mock import AsyncMock

import pytest

import build
from mind_nova import const


async def _empty_stream():
    if False:
        yield b""


class _CompletedTransport:
    def __init__(self) -> None:
        self.stdout = _empty_stream()
        self.stderr = _empty_stream()

    async def wait(self) -> None:
        return None


@pytest.mark.anyio
async def test_extensions_are_normalized_before_provider_bundle_is_copied(
        monkeypatch,
        tmp_path,
) -> None:
    app = tmp_path / "applications"
    source_bundle = app / f"{const.APP_NAME}.app"
    target = source_bundle / "Contents" / "MacOS"
    target.mkdir(parents=True)

    compiled_extension = target / "module.cpython-311-darwin.so"
    compiled_extension.write_bytes(b"application")

    schematic = tmp_path / const.SCHEMATIC
    resources = schematic / "resources"
    supports = schematic / "supports" / "macos"
    skills = schematic / "skills"
    launcher = resources / "automation" / f"{const.APP_NAME}.sh"
    provider_root = supports / "helix.app" / "Contents" / "MacOS"

    launcher.parent.mkdir(parents=True)
    skills.mkdir(parents=True)
    provider_root.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    provider_extension = provider_root / "module.cpython-311-darwin.so"
    provider_destination = provider_root / "module.so"
    provider_extension.write_bytes(b"provider-versioned")
    provider_destination.write_bytes(b"provider-stable")

    renamed_bundle = app / f"{const.APP_DESC}.app"
    packaging_result = (
        "darwin",
        app,
        tmp_path,
        target,
        (source_bundle, renamed_bundle),
        ["python", "-m", "nuitka"],
        (launcher, target),
        ["file", str(renamed_bundle / "Contents" / "MacOS" / const.APP_NAME)],
        "macos",
    )

    provider_copy = target / const.SCHEMATIC / "supports" / "macos" / "helix.app"
    observations: list[bool] = []
    normalize_extensions = build.rename_so_files

    async def observe_normalization(ops, output) -> None:
        observations.append(provider_copy.exists())
        await normalize_extensions(ops, output)

    monkeypatch.setattr(build, "packaging", AsyncMock(return_value=packaging_result))
    monkeypatch.setattr(build, "rename_so_files", observe_normalization)
    monkeypatch.setattr(build, "rename_sensitive", AsyncMock())
    monkeypatch.setattr(build, "authorized_tools", AsyncMock())
    monkeypatch.setattr(build, "edit_plist_fields", AsyncMock())
    monkeypatch.setattr(build, "report_binary_info", AsyncMock())
    monkeypatch.setattr(build.Terminal, "cmd_link", AsyncMock(return_value=_CompletedTransport()))

    await build.post_build()

    copied_provider_root = provider_copy / "Contents" / "MacOS"
    assert observations == [False]
    assert not compiled_extension.exists()
    assert (target / "module.so").read_bytes() == b"application"
    assert (copied_provider_root / provider_extension.name).read_bytes() == b"provider-versioned"
    assert (copied_provider_root / provider_destination.name).read_bytes() == b"provider-stable"
