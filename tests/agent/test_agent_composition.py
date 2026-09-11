# -*- coding: utf-8 -*-

import pytest
from pathlib import Path

from agent.composition import open_skills_provider


def test_open_skills_provider_uses_injected_payload_builder() -> None:
    """Skills provider 只协调读取和转换，不直接依赖基础设施实现。"""
    config = {"skills": {"enabled": ["review"], "disabled": []}}
    received: list[dict[str, object]] = []

    def build_payload(value: dict[str, object], workspace: Path) -> list[dict[str, str]]:
        assert workspace == Path("workspace")
        received.append(value)
        return [{"name": "review", "description": "Review changes"}]

    provider = open_skills_provider(
        lambda: config,
        lambda: Path("workspace"),
        payload_builder=build_payload,
    )

    assert provider() == [{"name": "review", "description": "Review changes"}]
    assert received == [config]


def test_open_skills_provider_requires_payload_builder() -> None:
    """缺少基础设施转换器时在组合边界显式失败。"""
    with pytest.raises(TypeError, match="skills payload builder"):
        open_skills_provider(lambda: {}, lambda: Path("workspace"), payload_builder=None)
