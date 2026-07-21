# -*- coding: utf-8 -*-

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
