# -*- coding: utf-8 -*-

import pytest

from agent.application.config.settings import AgentSettings
from infrastructure.config.schema import ConfigValidationError, normalize_config


def test_agent_settings_use_normalized_defaults() -> None:
    config = normalize_config({})

    assert config["agents"] == {
        "max_concurrent_threads_per_session": 4,
        "max_depth": 1,
        "default_fork_turns": 5,
        "max_fork_context_chars": 40000,
    }
    assert AgentSettings.from_config(config) == AgentSettings()


def test_agent_settings_read_explicit_config() -> None:
    config = normalize_config({
        "agents": {
            "max_concurrent_threads_per_session": 8,
            "max_depth": 2,
            "default_fork_turns": 3,
            "max_fork_context_chars": 12000,
        },
    })

    assert AgentSettings.from_config(config) == AgentSettings(
        max_concurrent_threads_per_session=8,
        max_depth=2,
        default_fork_turns=3,
        max_fork_context_chars=12000,
    )


@pytest.mark.parametrize(
    ("agents", "message"),
    [
        (
            {"max_concurrent_threads_per_session": 0},
            "must be a positive integer",
        ),
        ({"max_depth": -1}, "must be a non-negative integer"),
        ({"default_fork_turns": 0}, "must be a positive integer"),
        ({"max_fork_context_chars": 0}, "must be a positive integer"),
        ({"unknown": True}, "unknown agents key"),
    ],
)
def test_invalid_agent_settings_are_rejected(agents, message) -> None:
    with pytest.raises(ConfigValidationError, match=message):
        normalize_config({"agents": agents})
