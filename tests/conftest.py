# -*- coding: utf-8 -*-

import pytest
from pathlib import Path


@pytest.fixture
def repository_root(pytestconfig: pytest.Config) -> Path:
    """返回 pytest 已确认的仓库根目录。"""
    return pytestconfig.rootpath.resolve()


@pytest.fixture
def fixtures_root(repository_root: Path) -> Path:
    """返回版本化测试输入目录。"""
    return repository_root / "tests" / "fixtures"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
