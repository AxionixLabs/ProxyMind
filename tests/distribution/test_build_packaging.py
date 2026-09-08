# -*- coding: utf-8 -*-

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import build
from metadata import const


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
async def test_macos_bundle_assets_remove_quarantine_and_limit_chmod(
    monkeypatch,
    tmp_path,
) -> None:
    bundle = tmp_path / "Mind.app"
    executable = bundle / "Contents" / "MacOS" / "mind"
    document = bundle / "Contents" / "Resources" / "README.md"
    executable.parent.mkdir(parents=True)
    document.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")
    document.write_text("documentation", encoding="utf-8")

    removed_roots = []
    permission_updates: list[tuple[Path, int]] = []

    def remove_quarantine(root) -> None:
        removed_roots.append(root)

    def record_chmod(path: Path, mode: int) -> None:
        permission_updates.append((path, mode))

    monkeypatch.setattr(
        build,
        "remove_macos_quarantine_tree",
        remove_quarantine,
    )
    monkeypatch.setattr(Path, "chmod", record_chmod)

    await build.prepare_macos_bundle_assets(
        "darwin",
        bundle,
        (executable,),
    )

    assert removed_roots == [bundle]
    assert permission_updates == [(executable, 0o755)]


@pytest.mark.anyio
async def test_macos_bundle_assets_reject_missing_executable(tmp_path) -> None:
    bundle = tmp_path / "Mind.app"
    bundle.mkdir()
    missing = bundle / "Contents" / "MacOS" / "mind"

    with pytest.raises(build.AppError, match="macOS 可执行文件缺失"):
        await build.prepare_macos_bundle_assets(
            "darwin",
            bundle,
            (missing,),
        )


@pytest.mark.anyio
async def test_macos_bundle_assets_skip_other_platforms(
    monkeypatch,
    tmp_path,
) -> None:
    def unexpected_quarantine_removal(_root) -> None:
        raise AssertionError("macOS quarantine cleanup must not run")

    monkeypatch.setattr(
        build,
        "remove_macos_quarantine_tree",
        unexpected_quarantine_removal,
    )

    await build.prepare_macos_bundle_assets(
        "win32",
        tmp_path / "missing-app",
        (tmp_path / "missing-executable",),
    )


def test_macos_bundle_executables_include_all_routed_tools(tmp_path) -> None:
    target = tmp_path / "Mind.app" / "Contents" / "MacOS"
    launcher = tmp_path / "mind.sh"

    executable_paths = set(build.macos_bundle_executables(target, launcher))
    supports = target / const.SCHEMATIC / const.SUPPORTS / "macos"

    assert {
        supports / folder_name / command_name
        for folder_name, command_name in build.SHELL_TOOL_LAYOUT.values()
    } <= executable_paths
    assert supports / "ast-grep" / "sg" in executable_paths
    assert (
        supports / "helix.app" / "Contents" / "MacOS" / "helix"
    ) in executable_paths
    assert (
        target
        / const.SCHEMATIC
        / "sandbox"
        / "macos"
        / "bin"
        / "mind_sandbox_server"
    ) in executable_paths


def test_windows_sandbox_runtime_requires_all_three_binaries(tmp_path) -> None:
    sandbox = tmp_path / "sandbox" / "windows"
    binary_dir = sandbox / "bin"
    binary_dir.mkdir(parents=True)
    expected = tuple(
        binary_dir / name
        for name in build.SANDBOX_RUNTIME_ASSETS["win32"]
    )
    for binary in expected:
        binary.write_bytes(b"binary")

    assert build.validate_sidecar_assets("win32", sandbox) == expected


@pytest.mark.parametrize(
    "missing_name",
    [
        "mind_sandbox_server.exe",
        "mind-command-runner.exe",
        "mind-windows-sandbox-setup.exe",
    ],
)
def test_windows_sandbox_runtime_rejects_missing_helper(
    tmp_path,
    missing_name,
) -> None:
    sandbox = tmp_path / "sandbox" / "windows"
    binary_dir = sandbox / "bin"
    binary_dir.mkdir(parents=True)
    for name in build.SANDBOX_RUNTIME_ASSETS["win32"]:
        if name != missing_name:
            (binary_dir / name).write_bytes(b"binary")

    with pytest.raises(build.AppError, match=missing_name):
        build.validate_sidecar_assets("win32", sandbox)


def test_macos_sandbox_runtime_rejects_missing_sidecar(tmp_path) -> None:
    sandbox = tmp_path / "sandbox" / "macos"
    sandbox.mkdir(parents=True)

    with pytest.raises(
        build.AppError,
        match="mind_sandbox_server",
    ):
        build.validate_sidecar_assets("darwin", sandbox)


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
    sidecar = schematic / "sandbox" / "macos" / "bin" / "mind_sandbox_server"
    launcher = resources / "automation" / f"{const.APP_NAME}.sh"
    provider_root = supports / "helix.app" / "Contents" / "MacOS"

    launcher.parent.mkdir(parents=True)
    skills.mkdir(parents=True)
    sidecar.parent.mkdir(parents=True)
    provider_root.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    sidecar.write_bytes(b"sidecar")
    sidecar.chmod(0o755)

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
    monkeypatch.setattr(build, "prepare_macos_bundle_assets", AsyncMock())
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
