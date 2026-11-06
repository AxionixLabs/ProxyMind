# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

"""校验构建阶段收集的运行资产，不包含原生源码和构建中间目录。"""

import json
from pathlib import Path

from agent.protocol.json_value import ThawedJsonValue


def desktop_runtime_files(published: Path) -> tuple[Path, ...]:
    """验证 Windows x64 自包含发布目录，并返回其全部运行文件。"""
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
