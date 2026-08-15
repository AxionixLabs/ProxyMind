# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

DEFAULT_MAX_CONCURRENT_THREADS = 4
DEFAULT_MAX_AGENT_DEPTH        = 1
DEFAULT_FORK_TURNS             = 5
DEFAULT_MAX_FORK_CONTEXT_CHARS = 40_000

AGENT_CONFIG_FIELDS = frozenset({
    "max_concurrent_threads_per_session",
    "max_depth",
    "default_fork_turns",
    "max_fork_context_chars",
})


class AgentConfigError(ValueError):
    """表示本地多执行主体配置不符合约束。"""


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """保存本地多执行主体运行设置。"""
    max_concurrent_threads_per_session: int = DEFAULT_MAX_CONCURRENT_THREADS
    max_depth: int = DEFAULT_MAX_AGENT_DEPTH
    default_fork_turns: int = DEFAULT_FORK_TURNS
    max_fork_context_chars: int = DEFAULT_MAX_FORK_CONTEXT_CHARS

    @classmethod
    def from_config(cls, config: typing.Any) -> "AgentSettings":
        """从有效配置快照读取运行设置。"""
        root   = config if isinstance(config, dict) else {}
        values = normalize_agent_table(root.get("agents"))

        return cls(
            max_concurrent_threads_per_session=(
                values["max_concurrent_threads_per_session"]
            ),
            max_depth=values["max_depth"],
            default_fork_turns=values["default_fork_turns"],
            max_fork_context_chars=values["max_fork_context_chars"],
        )


def normalize_agent_table(raw: typing.Any) -> dict[str, typing.Any]:
    """校验并规范化本地多执行主体配置。"""
    if raw is None:
        data: dict[str, typing.Any] = {}
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        raise AgentConfigError("agents must be a table")

    unknown = sorted(set(data).difference(AGENT_CONFIG_FIELDS))
    if unknown:
        raise AgentConfigError(f"unknown agents key: {unknown[0]}")

    max_threads = _positive_integer(
        data.get(
            "max_concurrent_threads_per_session",
            DEFAULT_MAX_CONCURRENT_THREADS,
        ),
        "agents.max_concurrent_threads_per_session",
    )
    max_depth = _non_negative_integer(
        data.get("max_depth", DEFAULT_MAX_AGENT_DEPTH),
        "agents.max_depth",
    )
    default_fork_turns = _positive_integer(
        data.get("default_fork_turns", DEFAULT_FORK_TURNS),
        "agents.default_fork_turns",
    )
    max_fork_context_chars = _positive_integer(
        data.get(
            "max_fork_context_chars",
            DEFAULT_MAX_FORK_CONTEXT_CHARS,
        ),
        "agents.max_fork_context_chars",
    )

    return {
        "max_concurrent_threads_per_session": max_threads,
        "max_depth": max_depth,
        "default_fork_turns": default_fork_turns,
        "max_fork_context_chars": max_fork_context_chars,
    }


def _positive_integer(value: typing.Any, field: str) -> int:
    """校验并返回正整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AgentConfigError(f"{field} must be a positive integer")
    return value


def _non_negative_integer(value: typing.Any, field: str) -> int:
    """校验并返回非负整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentConfigError(f"{field} must be a non-negative integer")
    return value


if __name__ == '__main__':
    pass
