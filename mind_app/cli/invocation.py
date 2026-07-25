# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import argparse
from mind_core.config import (
    ConfigOverride,
    parse_config_override
)
from mind_core.config_layers import normalize_profile_name
from mind_nova import const

CONFIG_FLAGS  = ("-c", "--config")
PROFILE_FLAGS = ("-p", "--profile")

VALUE_OPTIONS = frozenset((
    *CONFIG_FLAGS,
    *PROFILE_FLAGS,
))


class ArgumentContainer(typing.Protocol):
    """描述能够登记 argparse 选项的容器。"""

    def add_argument(
        self,
        *name_or_flags: str,
        **kwargs: typing.Any,
    ) -> argparse.Action:
        """登记一个命令行参数。"""
        ...


def add_invocation_options(container: ArgumentContainer) -> None:
    """登记进程级配置覆盖选项。"""
    container.add_argument(
        *CONFIG_FLAGS,
        action="append",
        metavar="key=value",
        help=(
            "Override a configuration value that would otherwise be loaded "
            f"from `~/.{const.APP_NAME}/config.toml`. Use a dotted path "
            "(`foo.bar.baz`) to override nested values. The value portion is "
            "parsed as TOML. If it fails to parse as TOML, the raw string is "
            "used as a literal.\n\n"
            "Examples: - `-c model=\"o3\"` - "
            "`-c 'skills.disabled=[\"legacy\"]'` - "
            "`-c hosted_tools.groups.perf_engine=true`"
        ),
    )
    container.add_argument(
        *PROFILE_FLAGS,
        metavar="PROFILE",
        help=(
            f"Layer `~/.{const.APP_NAME}/<name>.config.toml` on top of "
            "the base user configuration"
        ),
    )


def extract_invocation_options(
    parser: argparse.ArgumentParser,
    arguments: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[ConfigOverride, ...], str | None]:
    """提取可出现在任意命令层级的进程级选项。"""
    remaining: list[str]            = []
    overrides: list[ConfigOverride] = []
    profile: str | None             = None

    index: int = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            remaining.extend(arguments[index:])
            break

        option, value, consumed = _option_value(arguments, index)
        if option in VALUE_OPTIONS and value is None:
            parser.error(f"argument {option}: expected one argument")
        if option is None or value is None:
            remaining.append(token)
            index += 1
            continue

        try:
            if option in CONFIG_FLAGS:
                overrides.append(parse_config_override(value))
            elif option in PROFILE_FLAGS:
                if profile is not None:
                    raise ValueError("profile may only be specified once")
                profile = normalize_profile_name(value)
        except ValueError as error:
            parser.error(str(error))
        index += consumed

    return tuple(remaining), tuple(overrides), profile


def _option_value(
    arguments: tuple[str, ...],
    index: int,
) -> tuple[str | None, str | None, int]:
    """读取当前位置的全局选项、参数值和消费长度。"""
    token = arguments[index]
    if token in VALUE_OPTIONS:
        if index + 1 >= len(arguments):
            return token, None, 1
        return token, arguments[index + 1], 2

    for option in ("--config", "--profile"):
        prefix = f"{option}="
        if token.startswith(prefix):
            return option, token[len(prefix):], 1

    return None, None, 1


if __name__ == "__main__":
    pass
