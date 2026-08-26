# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import (
    dataclass,
    field
)
from pathlib import Path
from typing import (
    Iterable,
    Sequence
)
from urllib.parse import urlparse
from .decision import Decision
from .rule import (
    HostExecutable,
    NetworkRule,
    NetworkRuleProtocol,
    PrefixRule,
    RuleMatch,
)


@dataclass(frozen=True, slots=True)
class MatchOptions:
    """控制策略匹配时使用的可选上下文。"""
    resolve_host_executables: bool = False
    host_executable_paths: tuple[str, ...] = ()
    network_protocol: NetworkRuleProtocol | str | None = None


@dataclass(frozen=True, slots=True)
class Evaluation:
    """保存一次策略评估的决定和命中规则。"""
    decision: Decision | None = None
    matched_rules: tuple[object, ...] = ()

    @property
    def is_match(self) -> bool:
        """判断是否至少命中一条策略。"""
        return bool(self.matched_rules)

    @property
    def matched_rule(self) -> object | None:
        """返回第一条命中规则，便于调用方使用。"""
        return self.matched_rules[0] if self.matched_rules else None


@dataclass
class Policy:
    """保存前缀规则、网络规则和宿主可执行文件列表。"""
    prefix_rules: list[PrefixRule] = field(default_factory=list)
    network_rules: list[NetworkRule] = field(default_factory=list)
    host_executables: list[HostExecutable] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "Policy":
        """创建空策略。"""
        return cls()

    @classmethod
    def from_parts(
        cls,
        prefix_rules: Iterable[PrefixRule] = (),
        network_rules: Iterable[NetworkRule] = (),
        host_executables: Iterable[HostExecutable] = ()
    ) -> "Policy":
        """从各类规则创建策略。"""
        return cls(list(prefix_rules), list(network_rules), list(host_executables))

    def _network_matches(
        self,
        command: Sequence[str],
        options: MatchOptions
    ) -> list[NetworkRule]:
        candidates: list[tuple[str, NetworkRuleProtocol]] = []
        if options.network_protocol is not None:
            try:
                protocol = NetworkRuleProtocol.parse(options.network_protocol)
            except ValueError:
                protocol = None
            if protocol is not None:
                for word in command:
                    parsed = urlparse(word)
                    if parsed.hostname:
                        candidates.append((parsed.hostname, protocol))
        else:
            for word in command:
                parsed = urlparse(word)
                if not parsed.hostname or parsed.scheme not in {"http", "https"}:
                    continue
                protocol = NetworkRuleProtocol.parse(parsed.scheme)
                candidates.append((parsed.hostname, protocol))
        return [
            rule
            for host, protocol in candidates
            for rule in self.network_rules
            if rule.matches(host, protocol)
        ]

    def add_prefix_rule(self, rule: PrefixRule) -> None:
        """追加一条命令前缀规则。"""
        self.prefix_rules.append(rule)

    def add_network_rule(self, rule: NetworkRule) -> None:
        """追加一条网络规则。"""
        self.network_rules.append(rule)

    def set_host_executable_paths(self, paths: Iterable[str | Path]) -> None:
        """替换宿主可执行文件列表。"""
        self.host_executables = [HostExecutable(Path(path)) for path in paths]

    def merge_overlay(self, overlay: "Policy") -> "Policy":
        """把更高优先级的策略规则叠加到当前策略。"""
        self.prefix_rules.extend(overlay.prefix_rules)
        self.network_rules.extend(overlay.network_rules)
        self.host_executables.extend(overlay.host_executables)
        return self

    def matches_for_command_with_options(
        self,
        command: Sequence[str],
        options: MatchOptions | None = None
    ) -> tuple[object, ...]:
        """返回命令匹配的所有规则。"""
        normalized = tuple(str(word) for word in command if str(word))
        network_options = options or MatchOptions()
        matches: list[object] = [
            rule for rule in self.prefix_rules
            if rule.matches(
                normalized,
                resolve_host_executables=network_options.resolve_host_executables,
                host_executable_paths=network_options.host_executable_paths,
            )
        ]
        matches.extend(self._network_matches(normalized, network_options))
        return tuple(matches)

    def matches_for_command(self, command: Sequence[str]) -> tuple[object, ...]:
        """返回命令匹配的所有规则。"""
        return self.matches_for_command_with_options(command)

    def check_with_options(
        self,
        command: Sequence[str],
        options: MatchOptions | None = None,
        *,
        heuristics_fallback: Decision | None = None
    ) -> Evaluation:
        """按规则和可选启发式结果评估单条命令。"""
        matches = self.matches_for_command_with_options(command, options)
        decisions = [
            getattr(match, "decision", None)
            for match in matches
        ]
        decision = Decision.strictest(*decisions)
        if decision is None:
            decision = heuristics_fallback
        return Evaluation(decision=decision, matched_rules=matches)

    def check(
        self,
        command: Sequence[str],
        *,
        heuristics_fallback: Decision | None = None
    ) -> Evaluation:
        """评估单条命令。"""
        return self.check_with_options(
            command,
            heuristics_fallback=heuristics_fallback,
        )

    def check_multiple_with_options(
        self,
        commands: Iterable[Sequence[str]],
        options: MatchOptions | None = None,
        *,
        heuristics_fallback: Decision | None = None
    ) -> Evaluation:
        """评估一组 shell 中的命令并合并结果。"""
        all_matches: list[object] = []
        decisions: list[Decision] = []
        for command in commands:
            result = self.check_with_options(
                command,
                options,
                heuristics_fallback=heuristics_fallback,
            )
            all_matches.extend(result.matched_rules)
            if result.decision is not None:
                decisions.append(result.decision)
        return Evaluation(
            decision=Decision.strictest(*decisions),
            matched_rules=tuple(all_matches),
        )

    def check_multiple(
        self,
        commands: Iterable[Sequence[str]],
        *,
        heuristics_fallback: Decision | None = None
    ) -> Evaluation:
        """评估一组命令。"""
        return self.check_multiple_with_options(
            commands,
            heuristics_fallback=heuristics_fallback,
        )


if __name__ == "__main__":
    pass
