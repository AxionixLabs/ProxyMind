# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

"""校验构建阶段收集的运行资产，不包含原生源码和构建中间目录。"""

import json
import plistlib
import struct
from pathlib import Path

from agent.protocol.json_value import ThawedJsonValue


MACOS_DESKTOP_BUNDLE_ID = "com.axionixlabs.desktop-sidecar"
MACOS_DESKTOP_EXECUTABLE = "DesktopSidecar.app/Contents/MacOS/desktop-sidecar"


def _macos_runtime_files(published: Path) -> tuple[Path, ...]:
    """检查 ARM64 Mach-O、部署版本与完整 bundle，拒绝外部链接依赖。"""
    bundle = published / "DesktopSidecar.app"
    executable = published / MACOS_DESKTOP_EXECUTABLE
    with (bundle / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    expected: dict[str, str | bool] = {
        "CFBundleIdentifier": MACOS_DESKTOP_BUNDLE_ID, "CFBundleExecutable": "desktop-sidecar",
        "CFBundlePackageType": "APPL", "LSMinimumSystemVersion": "14.2", "LSUIElement": True,
    }
    if not isinstance(info, dict) or any(type(info.get(name)) is not type(value) or info.get(name) != value
                                         for name, value in expected.items()):
        raise ValueError("CUA bundle identity or deployment metadata is invalid")
    with executable.open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise ValueError("CUA Mach-O header is missing")
        magic, cpu, _, kind, count, size, _, _ = struct.unpack("<8I", header)
        if magic != 0xFEEDFACF or cpu != 0x0100000C or kind != 2:
            raise ValueError("CUA requires a native ARM64 Mach-O executable")
        if size > 1024**2 or count > 4096:
            raise ValueError("CUA Mach-O load commands exceed the limit")
        commands = stream.read(size)
    offset = 0
    deployment = False
    for _ in range(count):
        if offset + 8 > len(commands):
            raise ValueError("CUA Mach-O load commands are truncated")
        command, length = struct.unpack_from("<2I", commands, offset)
        if length < 8 or offset + length > len(commands):
            raise ValueError("CUA Mach-O load command is invalid")
        if command == 0x32 and length >= 24:
            platform, minimum = struct.unpack_from("<2I", commands, offset + 8)
            deployment = platform == 1 and minimum == 0x000E0200
        if command in {0xC, 0x18 | 0x80000000, 0x1F | 0x80000000}:
            if length < 24:
                raise ValueError("CUA Mach-O library command is invalid")
            name_offset = struct.unpack_from("<I", commands, offset + 8)[0]
            if not 24 <= name_offset < length:
                raise ValueError("CUA Mach-O library name is invalid")
            name = commands[offset + name_offset:offset + length].split(b"\0", 1)[0]
            if not name.startswith((b"/usr/lib/", b"/System/Library/")):
                raise ValueError("CUA must not depend on a developer's local libraries")
        offset += length
    if offset != size or len(commands) != size or not deployment:
        raise ValueError("CUA requires the macOS 14.2 deployment target")
    files: list[Path] = []
    for path in sorted(published.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(published.resolve()):
            raise ValueError(f"Runtime asset escapes its published directory: {path}")
        if path == published / ".installed.json":
            continue
        if path != bundle and not path.is_relative_to(bundle):
            raise ValueError(f"Unexpected file outside CUA bundle: {path}")
        if path.is_file():
            files.append(path)
    if not (bundle / "Contents/_CodeSignature/CodeResources").is_file():
        raise ValueError("CUA bundle signature inventory is missing")
    if executable.stat().st_mode & 0o111 != 0o111:
        raise ValueError("CUA macOS executable permissions are missing")
    return tuple(files)


def desktop_runtime_files(published: Path, *, platform: str = "win32") -> tuple[Path, ...]:
    """验证平台原生发布目录，并返回全部运行文件。"""
    if platform == "darwin":
        return _macos_runtime_files(published)
    if platform != "win32":
        raise ValueError(f"Unsupported CUA runtime platform: {platform}")
    required = (
        "desktop-sidecar.exe", "desktop-sidecar.dll", "desktop-sidecar.deps.json",
        "desktop-sidecar.runtimeconfig.json", "coreclr.dll", "hostfxr.dll", "hostpolicy.dll",
        "System.Private.CoreLib.dll", "PresentationFramework.dll",
    )
    for name in required:
        if not (published / name).is_file():
            raise ValueError(f"Missing self-contained desktop runtime asset: {published / name}")
    value: ThawedJsonValue = json.loads((published / "desktop-sidecar.runtimeconfig.json").read_text(encoding="utf-8"))
    options = value.get("runtimeOptions") if isinstance(value, dict) else None
    if not isinstance(options, dict) or "framework" in options or "frameworks" in options:
        raise ValueError("Desktop release must include its .NET runtime")
    included = options.get("includedFrameworks")
    if not isinstance(included, list):
        raise ValueError("Desktop runtime framework inventory is missing")
    names: set[str] = set()
    for framework in included:
        if not isinstance(framework, dict):
            raise ValueError("Invalid desktop runtime framework inventory")
        name = framework.get("name")
        if not isinstance(name, str):
            raise ValueError("Invalid desktop runtime framework name")
        names.add(name)
    if names != {"Microsoft.NETCore.App", "Microsoft.WindowsDesktop.App"}:
        raise ValueError("Desktop runtime framework inventory is incomplete")
    files = tuple(sorted(path for path in published.rglob("*")
                         if path.is_file() and path.suffix != ".pdb" and path.name != ".installed.json"))
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(published.resolve()):
            raise ValueError(f"Runtime asset escapes its published directory: {path}")
    return files


def runtime_asset_files(asset_root: Path) -> tuple[Path, ...]:
    """收集随主包分发的共用 JS，原生运行库由独立清单按需下载。"""
    javascript = (
        asset_root / "js_repl" / "kernel.js",
        asset_root / "js_repl" / "vendor" / "meriyah.umd.min.js",
    )
    for path in javascript:
        if not path.is_file():
            raise ValueError(f"Missing JavaScript runtime asset: {path}")
    return javascript


if __name__ == '__main__':
    pass
