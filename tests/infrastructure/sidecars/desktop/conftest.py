import sys
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """显式启用会打开专用测试窗口的原生桌面验收。"""
    parser.addoption("--cua-native", action="store_true", help="Run desktop tests against the native GUI fixture")


@pytest.fixture
def native_executable(repository_root: Path, pytestconfig: pytest.Config) -> Path:
    """定位原生测试共用的 Windows 资产，未启用时明确跳过。"""
    if not pytestconfig.getoption("--cua-native") or sys.platform != "win32":
        pytest.skip("Requires --cua-native and an interactive Windows desktop")
    executable = repository_root / "sidecars/cua_windows/publish/desktop-sidecar.exe"
    assert executable.is_file(), "Publish the native sidecar first"
    return executable
