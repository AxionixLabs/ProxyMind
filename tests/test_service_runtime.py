# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path

import pytest

from engine.tinker import MindError
from mind_app.runtime.mcp import service_runtime as runtime


def run_async(value: object) -> object:
    """同步测试中运行异步逻辑。"""
    return asyncio.run(value)


def runtime_context(tmp_path: Path, *, asset_exists: bool = False) -> runtime.ServiceRuntimeContext:
    """生成服务运行时测试上下文。"""
    supports = tmp_path / "supports"
    executable = supports / "helix.dist" / "helix.exe"
    if asset_exists:
        executable.parent.mkdir(parents=True)
        executable.write_text("", encoding="utf-8")

    spec = runtime.ServiceRuntimeSpec(
        supports=str(supports),
        executable=str(executable),
        launch_command=[str(executable)],
        path_entries=(str(executable.parent),)
    )
    return runtime.ServiceRuntimeContext(
        spec=spec,
        platform="win32",
        software="mind.exe",
        packaged=True,
        env_symbol=";",
        app_desc="Mind"
    )


def test_prepare_service_runtime_rejects_missing_asset_without_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """非交互环境不会静默下载缺失的运行时资产。"""
    context = runtime_context(tmp_path)
    called = {"ensure": False}

    async def ensure_asset(*_: object, **__: object) -> bool:
        called["ensure"] = True
        return True

    monkeypatch.setattr(runtime, "can_prompt_runtime_download", lambda: False)
    monkeypatch.setattr(runtime, "ensure_service_runtime_asset", ensure_asset)

    with pytest.raises(MindError, match="Helix runtime missing"):
        run_async(runtime.prepare_service_runtime(context, anim_manager=object()))

    assert called["ensure"] is False


def test_prepare_service_runtime_skips_when_user_declines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户取消下载时跳过准备流程但不抛出失败。"""
    context = runtime_context(tmp_path)
    called = {"ensure": False}

    async def decline(*_: object, **__: object) -> bool:
        return False

    async def ensure_asset(*_: object, **__: object) -> bool:
        called["ensure"] = True
        return True

    monkeypatch.setattr(runtime, "can_prompt_runtime_download", lambda: True)
    monkeypatch.setattr(runtime, "choose_runtime_download", decline)
    monkeypatch.setattr(runtime, "ensure_service_runtime_asset", ensure_asset)

    prepared = run_async(runtime.prepare_service_runtime(context, anim_manager=object()))

    assert prepared is False
    assert called["ensure"] is False


def test_prepare_service_runtime_downloads_after_user_accepts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户确认下载后继续执行运行时准备流程。"""
    context = runtime_context(tmp_path)
    calls: list[str] = []

    async def approve(*_: object, **__: object) -> bool:
        calls.append("prompt")
        return True

    async def ensure_asset(*_: object, **__: object) -> bool:
        calls.append("ensure")
        return True

    async def authorize(*_: object, **__: object) -> None:
        calls.append("authorize")

    monkeypatch.setattr(runtime, "can_prompt_runtime_download", lambda: True)
    monkeypatch.setattr(runtime, "choose_runtime_download", approve)
    monkeypatch.setattr(runtime, "ensure_service_runtime_asset", ensure_asset)
    monkeypatch.setattr(runtime, "authorize_runtime_files", authorize)
    monkeypatch.setattr(runtime, "verify_runtime_paths", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "prepend_runtime_paths", lambda *_args, **_kwargs: calls.append("path"))

    prepared = run_async(runtime.prepare_service_runtime(context, anim_manager=object()))

    assert prepared is True
    assert calls == ["prompt", "ensure", "path", "authorize"]


def test_prepare_service_runtime_skips_prompt_when_asset_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """运行时资产已存在时不会显示下载确认菜单。"""
    context = runtime_context(tmp_path, asset_exists=True)
    calls: list[str] = []

    async def fail_prompt(*_: object, **__: object) -> bool:
        raise AssertionError("prompt should not be shown")

    async def ensure_asset(*_: object, **__: object) -> bool:
        calls.append("ensure")
        return False

    async def authorize(*_: object, **__: object) -> None:
        calls.append("authorize")

    monkeypatch.setattr(runtime, "can_prompt_runtime_download", lambda: True)
    monkeypatch.setattr(runtime, "choose_runtime_download", fail_prompt)
    monkeypatch.setattr(runtime, "ensure_service_runtime_asset", ensure_asset)
    monkeypatch.setattr(runtime, "authorize_runtime_files", authorize)
    monkeypatch.setattr(runtime, "prepend_runtime_paths", lambda *_args, **_kwargs: calls.append("path"))

    prepared = run_async(runtime.prepare_service_runtime(context, anim_manager=object()))

    assert prepared is True
    assert calls == ["ensure", "path", "authorize"]
