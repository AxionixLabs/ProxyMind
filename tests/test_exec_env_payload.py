# -*- coding: utf-8 -*-

import asyncio
import os

from mind_app.runtime.environment import exec_env as runtime_env
from mind_nova.requests.payload import build_chat_payload


def run_async(value: object) -> object:
    """同步测试中运行异步逻辑。"""
    return asyncio.run(value)


def test_build_runtime_exec_env_keeps_helix_under_provider(
    monkeypatch
) -> None:
    """Helix 环境只挂在 providers.helix，不污染 Mind 顶层。"""
    monkeypatch.setattr(
        runtime_env,
        "exec_env",
        lambda: {
            "platform": {"system": "darwin"},
            "tools": {"rg": {"available": True}},
            "providers": {"other": {"ok": True}}
        }
    )

    source = {"tools": {"adb": {"available": True}}}
    data = runtime_env.build_runtime_exec_env(service_exec_env=source)

    assert data["platform"] == {"system": "darwin"}
    assert data["tools"] == {"rg": {"available": True}}
    assert data["providers"]["other"] == {"ok": True}
    assert data["providers"]["helix"] == source

    source["tools"]["adb"]["available"] = False
    assert data["providers"]["helix"]["tools"]["adb"]["available"] is True


def test_build_chat_payload_does_not_probe_runtime_env() -> None:
    """请求层只组装载荷，未注入 exec_env 时使用空对象。"""
    payload = run_async(
        build_chat_payload(
            "chat",
            {"model": "test"},
            "hello",
            [],
            skills=[]
        )
    )

    assert payload["exec_env"] == {}


def test_detect_workspace_reports_dynamic_project_envs(
    tmp_path,
    monkeypatch
) -> None:
    """workspace 按当前项目标记上报多生态项目环境。"""
    (tmp_path / "pom.xml").write_text("<project />\n", encoding="utf-8")
    (tmp_path / "mvnw").write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module demo\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    venv_bin = tmp_path / "venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_bin.mkdir(parents=True)
    (venv_bin / ("python.exe" if os.name == "nt" else "python")).write_text(
        "",
        encoding="utf-8"
    )

    monkeypatch.chdir(tmp_path)

    data = runtime_env.detect_workspace()
    projects = data["projects"]

    assert data["root"] == str(tmp_path.resolve())
    assert "python_virtualenvs" not in data
    assert data["markers"] == [
        "pyproject.toml",
        "package.json",
        "pnpm-lock.yaml",
        "pom.xml",
        "mvnw",
        "go.mod",
        "Cargo.toml"
    ]
    assert projects["python"]["virtualenvs"][0]["relative_python"].startswith("venv/")
    assert projects["node"]["package_manager"] == "pnpm"
    assert projects["java"]["build_tools"] == ["maven"]
    assert projects["java"]["maven_wrapper"]["relative_path"] == "mvnw"
    assert projects["go"]["module_file"]["relative_path"] == "go.mod"
    assert projects["rust"]["manifest"]["relative_path"] == "Cargo.toml"
