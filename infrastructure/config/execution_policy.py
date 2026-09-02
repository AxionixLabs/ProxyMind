# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ast
import typing
from dataclasses import (
    dataclass,
    field
)
from pathlib import Path

from agent.domain.execution_policy import (
    Decision,
    Policy,
)
from agent.domain.execution_policy.rule import (
    NetworkRule,
    NetworkRuleProtocol,
    PrefixRule,
    PrefixPattern,
    HostExecutable
)
from metadata import const


@dataclass
class PolicyParser:
    """使用 Python AST 安全解析规则文件，不执行规则文件代码。"""
    source: str = ""
    filename: str = "<rules>"
    warnings: list[str] = field(default_factory=list)

    @staticmethod
    def _required_text(value: typing.Any, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    @staticmethod
    def _optional_text(value: typing.Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @classmethod
    def new(cls, source: str = "", filename: str = "<rules>") -> "PolicyParser":
        """创建规则解析器。"""
        return cls(source=source, filename=filename)

    @classmethod
    def from_file(cls, path: str | Path) -> "PolicyParser":
        """从文件创建规则解析器。"""
        target = Path(path)
        return cls(target.read_text(encoding=const.CHARSET), str(target))

    def _arguments(self, call: ast.Call) -> dict[str, typing.Any]:
        if call.args:
            raise ValueError("positional rule arguments are not supported")
        values: dict[str, typing.Any] = {}
        for keyword in call.keywords:
            if keyword.arg is None:
                raise ValueError("rule argument expansion is not supported")
            values[keyword.arg] = ast.literal_eval(keyword.value)
        return values

    def _parse_statement(self, statement: ast.stmt, policy: Policy) -> None:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            return
        call = statement.value
        if not isinstance(call.func, ast.Name):
            return

        name = call.func.id
        kwargs = self._arguments(call)

        if name == "prefix_rule":
            pattern = kwargs.get("pattern")
            if isinstance(pattern, str):
                pattern = [pattern]
            if not isinstance(pattern, (list, tuple)):
                raise ValueError("prefix_rule pattern must be a string or list")
            decision = Decision.parse(kwargs.get("decision", "allow"))
            policy.add_prefix_rule(PrefixRule(
                PrefixPattern.from_values(pattern),
                decision=decision,
                justification=self._optional_text(kwargs.get("justification")),
                source=self.filename,
            ))
            return

        if name == "network_rule":
            host = self._required_text(kwargs.get("host"), "network_rule host")
            protocol = NetworkRuleProtocol.parse(kwargs.get("protocol", "https"))
            decision = Decision.parse(kwargs.get("decision", "allow"))
            raw_port = kwargs.get("port")
            if raw_port is not None and (
                isinstance(raw_port, bool)
                or not isinstance(raw_port, int)
                or not 1 <= raw_port <= 65535
            ):
                raise ValueError("network_rule port is invalid")

            policy.add_network_rule(NetworkRule(
                host=host,
                protocol=protocol,
                decision=decision,
                justification=self._optional_text(kwargs.get("justification")),
                source=self.filename,
                port=raw_port,
            ))
            return
        if name == "host_executable":
            executable_name = self._optional_text(kwargs.get("name"))
            raw_paths = kwargs.get("paths")
            if isinstance(raw_paths, (list, tuple)):
                paths = tuple(
                    Path(self._required_text(path, "host_executable path"))
                    for path in raw_paths
                )
                if not paths:
                    raise ValueError("host_executable paths cannot be empty")
                for path in paths:
                    policy.host_executables.append(HostExecutable(
                        path,
                        name=executable_name,
                        paths=paths,
                        source=self.filename,
                    ))
                return
            path = kwargs.get("path", kwargs.get("executable"))
            policy.host_executables.append(HostExecutable(
                Path(self._required_text(path, "host_executable path")),
                name=executable_name,
                source=self.filename,
            ))

    def parse(self, source: str | None = None) -> Policy:
        """解析规则源文本并返回策略对象。"""
        text = self.source if source is None else source
        policy = Policy.empty()

        try:
            tree = ast.parse(text, filename=self.filename, mode="exec")
        except SyntaxError as error:
            self.warnings.append(f"{self.filename}: {error}")
            return policy

        for statement in tree.body:
            try:
                self._parse_statement(statement, policy)
            except (TypeError, ValueError) as error:
                self.warnings.append(f"{self.filename}: {error}")

        return policy

    def build(self) -> Policy:
        """按当前源文本构建策略。"""
        return self.parse()


if __name__ == '__main__':
    pass
