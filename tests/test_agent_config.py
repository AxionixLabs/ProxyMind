# -*- coding: utf-8 -*-

import pytest

from mind_core.agent_config import AgentSettings
from mind_core.config import ConfigValidationError, normalize_config


def test_agent_settings_use_normalized_defaults() -> None:
    config = normalize_config({})

    assert config["agents"] == {
        "enabled": True,
        "max_concurrent_threads_per_session": 4,
        "max_depth": 1,
    }
    assert AgentSettings.from_config(config) == AgentSettings()


def test_agent_settings_read_explicit_config() -> None:
    config = normalize_config({
        "agents": {
            "enabled": False,
            "max_concurrent_threads_per_session": 8,
            "max_depth": 2,
        },
    })

    assert AgentSettings.from_config(config) == AgentSettings(
        enabled=False,
        max_concurrent_threads_per_session=8,
        max_depth=2,
    )


@pytest.mark.parametrize(
    ("agents", "message"),
    [
        ({"enabled": 1}, "enabled must be a boolean"),
        (
            {"max_concurrent_threads_per_session": 0},
            "must be a positive integer",
        ),
        ({"max_depth": -1}, "must be a non-negative integer"),
        ({"unknown": True}, "unknown agents key"),
    ],
)
def test_invalid_agent_settings_are_rejected(agents, message) -> None:
    with pytest.raises(ConfigValidationError, match=message):
        normalize_config({"agents": agents})
