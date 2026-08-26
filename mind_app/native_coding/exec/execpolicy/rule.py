# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import (
    dataclass,
    field
)
from enum import Enum
from pathlib import Path
from typing import (
    Iterable,
    Sequence
)
from .decision import Decision


def _normalise_token(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True, slots=True)
class PatternToken:
    """表示前缀模式中的单值或候选值。"""

    value: str | None = None
    alternatives: tuple[str, ...] = ()

    @classmethod
    def single(cls, value: str) -> "PatternToken":
        """创建单值模式令牌。"""
        return cls(value=_normalise_token(value))

    @classmethod
    def Single(cls, value: str) -> "PatternToken":
        """使用枚举变体名称创建单值令牌。"""
        return cls.single(value)

    @classmethod
    def alts(cls, values: Iterable[str]) -> "PatternToken":
        """创建候选值模式令牌。"""
        return cls(alternatives=tuple(_normalise_token(item) for item in values))

    @classmethod
    def Alts(cls, values: Iterable[str]) -> "PatternToken":
        """使用枚举变体名称创建候选令牌。"""
        return cls.alts(values)

    def matches(self, value: str) -> bool:
        """判断一个命令词是否匹配当前令牌。"""
        normalized = _normalise_token(value)
        if self.value is not None:
            return normalized == self.value
        return normalized in self.alternatives

    def __post_init__(self) -> None:
        if self.value is None and not self.alternatives:
            raise ValueError("pattern token cannot be empty")
        if self.value is not None and self.alternatives:
            raise ValueError("pattern token cannot contain single and alternatives")


@dataclass(frozen=True, slots=True)
class PrefixPattern:
    """表示需要匹配命令开头的一组模式令牌。"""

    tokens: tuple[PatternToken, ...]

    @classmethod
    def from_values(cls, values: Sequence[object]) -> "PrefixPattern":
        """从字符串和字符串候选列表构建前缀模式。"""
        tokens: list[PatternToken] = []
        for value in values:
            if isinstance(value, (list, tuple)):
                tokens.append(PatternToken.alts(str(item) for item in value))
            else:
                tokens.append(PatternToken.single(str(value)))
        return cls(tuple(tokens))

    def matches_prefix(
        self,
        command: Sequence[str],
        *,
        resolve_host_executables: bool = False,
        host_executable_paths: Sequence[str] = (),
    ) -> bool:
        """判断命令是否以当前模式开头。"""
        if len(command) < len(self.tokens):
            return False
        for index, token in enumerate(self.tokens):
            value = command[index]
            if token.matches(value):
                continue
            if index == 0 and resolve_host_executables:
                basename = str(value).replace("\\", "/").rsplit("/", 1)[-1]
                basename = basename.casefold()
                basename = next(
                    (
                        basename[: -len(suffix)]
                        for suffix in (".exe", ".cmd", ".bat", ".com")
                        if basename.endswith(suffix)
                    ),
                    basename,
                )
                configured = tuple(
                    str(path).replace("\\", "/").casefold()
                    for path in host_executable_paths
                )
                has_identity_rule = any(
                    path.rsplit("/", 1)[-1].removesuffix(".exe") == basename
                    for path in configured
                )
                identity_allowed = (
                    not has_identity_rule
                    or str(value).replace("\\", "/").casefold() in configured
                )
                if identity_allowed and token.matches(basename):
                    continue
            return False
        return True

    def __len__(self) -> int:
        return len(self.tokens)


class RuleMatch(Enum):
    """标记规则匹配来源。"""

    PrefixRuleMatch = "prefix_rule"
    HeuristicsRuleMatch = "heuristics"

    @property
    def decision(self) -> Decision | None:
        """返回匹配来源自身携带的策略决定。"""
        return None


@dataclass(frozen=True, slots=True)
class PrefixRule:
    """表示一个命令前缀规则。"""

    pattern: PrefixPattern
    decision: Decision = Decision.Allow
    justification: str | None = None
    source: str | None = None

    def matches(
        self,
        command: Sequence[str],
        *,
        resolve_host_executables: bool = False,
        host_executable_paths: Sequence[str] = (),
    ) -> bool:
        """判断规则是否匹配命令。"""
        return self.pattern.matches_prefix(
            command,
            resolve_host_executables=resolve_host_executables,
            host_executable_paths=host_executable_paths,
        )


class NetworkRuleProtocol(str, Enum):
    """表示网络规则支持的协议。"""

    Http = "http"
    Https = "https"
    HttpsConnect = "https_connect"
    HttpConnect = "http-connect"
    Socks5Tcp = "socks5_tcp"
    Socks5Udp = "socks5_udp"

    @classmethod
    def parse(cls, value: object) -> "NetworkRuleProtocol":
        """解析规则文件中的网络协议名称。"""
        if isinstance(value, cls):
            return value
        normalized = _normalise_token(value).casefold().replace(" ", "_")

        aliases = {
            "http": cls.Http,
            "https": cls.Https,
            "https_connect": cls.HttpsConnect,
            "https-connect": cls.HttpsConnect,
            "http_connect": cls.HttpConnect,
            "http-connect": cls.HttpConnect,
            "socks5_tcp": cls.Socks5Tcp,
            "socks5-tcp": cls.Socks5Tcp,
            "socks5_udp": cls.Socks5Udp,
            "socks5-udp": cls.Socks5Udp,
        }

        try:
            return aliases[normalized]
        except KeyError as error:
            raise ValueError(f"unknown network protocol: {value!r}") from error


@dataclass(frozen=True, slots=True)
class NetworkRule:
    """表示针对主机和协议的网络策略规则。"""
    host: str
    protocol: NetworkRuleProtocol = NetworkRuleProtocol.Https
    decision: Decision = Decision.Allow
    justification: str | None = None
    source: str | None = None

    def matches(self, host: str, protocol: NetworkRuleProtocol | str) -> bool:
        """判断主机和协议是否匹配当前规则。"""
        try:
            normalized_protocol = NetworkRuleProtocol.parse(protocol)
        except ValueError:
            return False
        candidate = _normalise_token(host).casefold().rstrip(".")
        expected = _normalise_token(self.host).casefold().rstrip(".")
        return (
            normalized_protocol == self.protocol
            and (candidate == expected or candidate.endswith("." + expected))
        )


@dataclass(frozen=True, slots=True)
class HostExecutable:
    """保存规则文件声明的宿主可执行文件路径。"""
    path: Path
    name: str | None = None
    paths: tuple[Path, ...] = ()
    source: str | None = None


if __name__ == "__main__":
    pass
