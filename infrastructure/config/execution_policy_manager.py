# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import os
import re
import shlex
import threading
from collections.abc import (
    Iterable,
    Sequence,
)
from dataclasses import dataclass
from pathlib import Path

from agent.domain.execution_policy import (
    Decision,
    Evaluation,
    ExecutionPolicyAmendment,
    ExecutionPolicyRequirement,
    MatchOptions,
    NetworkRule,
    NetworkRuleProtocol,
    Policy,
    PrefixPattern,
    PrefixRule,
    SandboxPermission,
    normalize_sandbox_permission,
)
from agent.ports.network import NetworkRulePort
from infrastructure.config.execution_policy import PolicyParser
from infrastructure.config.paths import default_application_home
from infrastructure.platform.command_safety.is_dangerous_command import (
    DangerousCommandMatch,
    dangerous_command_match as _dangerous_command_match,
)
from metadata import const


@dataclass(frozen=True, slots=True)
class ExecApprovalCacheKey:
    """保存会话级命令批准的完整身份。"""
    tool: str
    command: tuple[str, ...]
    cwd: str
    environment_id: str
    tty: bool
    sandbox_permissions: SandboxPermission
    additional_permissions: str
    policy_fingerprint: str
    patch_scope: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplyPatchApprovalCacheKey:
    """保存补丁会话批准对应的环境和单个文件。"""
    environment_id: str
    path: str


def _stable_json(value: object) -> str:
    """将结构化值转换为稳定文本，供身份比较使用。"""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return str(value or "")


def _normalize_bool(value: object) -> bool:
    """按常见文本值规范化布尔字段。"""
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_patch_scope(value: object) -> tuple[str, ...]:
    """规范化补丁审批涉及的文件范围。"""
    if value is None:
        return ()
    if isinstance(value, (str, Path)):
        values = (str(value),)
    elif isinstance(value, Iterable):
        values = tuple(str(item) for item in value)
    else:
        values = (str(value),)
    return tuple(sorted({item.strip() for item in values if item.strip()}))


@dataclass(frozen=True, slots=True)
class ExecApprovalRequest:
    """描述一次需要本地执行策略判断的命令请求。"""
    command: tuple[str, ...]
    approval_policy: str = "on-request"
    sandbox_mode: str = "workspace-write"
    cwd: str = ""
    sandbox_permissions: SandboxPermission = "use_default"

    @classmethod
    def from_command(
        cls,
        command: Sequence[str] | str,
        *,
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        cwd: str = "",
        sandbox_permissions: object = "use_default",
    ) -> "ExecApprovalRequest":
        """从字符串或词元创建执行审批请求。"""
        words = _split_command(command)
        return cls(
            command=tuple(words),
            approval_policy=str(approval_policy or "on-request"),
            sandbox_mode=str(sandbox_mode or "workspace-write"),
            cwd=str(cwd or ""),
            sandbox_permissions=normalize_sandbox_permission(sandbox_permissions),
        )


class ExecPolicyManager:
    """加载本地规则并评估 shell/exec 工具命令。"""

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        *,
        policy: Policy | None = None,
        rules_paths: Iterable[str | Path] | None = None,
        writable_rules_path: str | Path | None = None
    ) -> None:
        """创建策略管理器并加载当前工作区的规则。"""
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve()
        self.warnings: list[str] = []

        self.writable_rules_path = Path(
            writable_rules_path
            or default_application_home() / "rules" / "default.rules"
        ).expanduser().resolve()

        self._session_approvals: set[ExecApprovalCacheKey] = set()
        self._session_patch_approvals: set[ApplyPatchApprovalCacheKey] = set()

        self._write_lock = threading.RLock()

        self.rules_paths = tuple(
            Path(path) for path in rules_paths
        ) if rules_paths is not None else self._discover_rules_paths()

        self.policy = policy or self._load_policy()

    @property
    def policy_fingerprint(self) -> str:
        """返回当前规则集合的稳定指纹。"""
        encoded = _stable_json(self.policy).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _rule_files(directories: Iterable[Path]) -> list[Path]:
        files: list[Path] = []
        seen: set[Path] = set()
        for directory in directories:
            for path in sorted(directory.glob("*.rules")):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(resolved)
        return files

    @classmethod
    def current(cls, workspace_root: str | Path | None = None) -> "ExecPolicyManager":
        """创建当前工作区的策略管理器。"""
        return cls(workspace_root=workspace_root)

    def decide(
        self,
        command: Sequence[str] | str,
        *,
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        cwd: str | Path | None = None,
        tool: str = "shell_command",
        sandbox_permissions: object = "use_default",
        environment_id: object = "",
        tty: object = False,
        additional_permissions: object = None,
        policy_fingerprint: object = None,
        patch_scope: object = None,
    ) -> Evaluation:
        """评估命令并返回策略决定。"""
        permission = normalize_sandbox_permission(sandbox_permissions)
        commands = commands_for_exec_policy(command)

        def exec_policy_fallback(parsed_command: Sequence[str]) -> Decision:
            return render_decision_for_unmatched_command(
                parsed_command,
                approval_policy=approval_policy,
                sandbox_mode=sandbox_mode,
                sandbox_permissions=permission,
            )

        evaluation = self.policy.check_multiple_with_options(
            commands,
            MatchOptions(
                resolve_host_executables=True,
                host_executable_paths=tuple(
                    str(item.path) for item in self.policy.host_executables
                )
            ),
            heuristics_fallback=exec_policy_fallback,
        )

        if evaluation.decision == Decision.Forbidden:
            return evaluation
        session_key = self._session_key(
            command,
            tool=tool,
            cwd=cwd,
            sandbox_permissions=permission,
            environment_id=environment_id,
            tty=tty,
            additional_permissions=additional_permissions,
            policy_fingerprint=policy_fingerprint,
            patch_scope=patch_scope,
        )
        with self._write_lock:
            session_approved = session_key in self._session_approvals
        if session_approved:
            return Evaluation(decision=Decision.Allow, matched_rules=())
        return evaluation

    def create_exec_approval_requirement_for_command(
        self,
        command: Sequence[str] | str,
        *,
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        cwd: str | Path | None = None,
        tool: str = "shell_command",
        amendment_id: str = "",
        sandbox_permissions: object = "use_default",
        environment_id: object = "",
        tty: object = False,
        additional_permissions: object = None,
        policy_fingerprint: object = None,
        patch_scope: object = None,
    ) -> ExecutionPolicyRequirement:
        """按三态模型生成本地执行要求。"""
        permission = normalize_sandbox_permission(sandbox_permissions)

        evaluation = self.decide(
            command,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
            cwd=cwd,
            tool=tool,
            sandbox_permissions=permission,
            environment_id=environment_id,
            tty=tty,
            additional_permissions=additional_permissions,
            policy_fingerprint=policy_fingerprint,
            patch_scope=patch_scope,
        )
        if evaluation.decision == Decision.Forbidden:
            if (
                not evaluation.matched_rules
                and str(approval_policy or "").strip().casefold() == "never"
            ):
                return ExecutionPolicyRequirement.forbidden(
                    "approval required by policy, but approval policy is never"
                )
            return ExecutionPolicyRequirement.forbidden(
                _evaluation_reason(evaluation, "local execution policy forbids command")
            )

        proposal = None
        if not _has_prompt_rule(evaluation):
            proposal = self._proposed_amendment_for_command(
                command,
                amendment_id=amendment_id,
            )

        if evaluation.decision == Decision.Prompt:
            if str(approval_policy or "").strip().casefold() == "never":
                return ExecutionPolicyRequirement.forbidden(
                    "approval required by policy, but approval policy is never"
                )
            return ExecutionPolicyRequirement.needs_approval(
                reason=_evaluation_reason(evaluation, "command requires approval"),
                proposed_execpolicy_amendment=(
                    _as_exec_policy_amendment(proposal)
                    if proposal is not None
                    else None
                ),
            )

        session_key = self._session_key(
            command,
            tool=tool,
            cwd=cwd,
            sandbox_permissions=permission,
            environment_id=environment_id,
            tty=tty,
            additional_permissions=additional_permissions,
            policy_fingerprint=policy_fingerprint,
            patch_scope=patch_scope,
        )
        with self._write_lock:
            session_approved = session_key in self._session_approvals
        if (
            permission == "require_escalated"
            and str(sandbox_mode or "workspace-write") != "danger-full-access"
            and not evaluation.matched_rules
            and not session_approved
        ):
            if str(approval_policy or "").strip().casefold() == "never":
                return ExecutionPolicyRequirement.forbidden(
                    "host shell execution requires approval, but approval policy is never"
                )
            return ExecutionPolicyRequirement.needs_approval(
                reason="require_escalated requests host shell execution",
                proposed_execpolicy_amendment=(
                    _as_exec_policy_amendment(proposal)
                    if proposal is not None
                    else None
                ),
            )

        return ExecutionPolicyRequirement.skip(
            bypass_sandbox=self._all_commands_explicitly_allowed(command),
            proposed_execpolicy_amendment=(
                _as_exec_policy_amendment(proposal)
                if proposal is not None
                else None
            ),
        )

    def add_approval_for_session(
        self,
        command: Sequence[str] | str,
        *,
        tool: str = "shell_command",
        cwd: str | Path | None = None,
        sandbox_permissions: object = "use_default",
        environment_id: object = "",
        tty: object = False,
        additional_permissions: object = None,
        policy_fingerprint: object = None,
        patch_scope: object = None,
    ) -> None:
        """在当前应用会话内精确批准一次命令形态。"""
        key = self._session_key(
            command,
            tool=tool,
            cwd=cwd,
            sandbox_permissions=sandbox_permissions,
            environment_id=environment_id,
            tty=tty,
            additional_permissions=additional_permissions,
            policy_fingerprint=policy_fingerprint,
            patch_scope=patch_scope,
        )
        if not key.command:
            raise ValueError("session approval command is required")
        with self._write_lock:
            self._session_approvals.add(key)

    def patch_scope_approved_for_session(
        self,
        patch_scope: object,
        *,
        cwd: str | Path | None = None,
        environment_id: object = "",
    ) -> bool:
        """判断补丁涉及的全部文件是否已获本会话批准。"""
        keys = self._patch_approval_keys(
            patch_scope,
            cwd=cwd,
            environment_id=environment_id,
        )
        if not keys:
            return False
        with self._write_lock:
            return all(key in self._session_patch_approvals for key in keys)

    def add_patch_approval_for_session(
        self,
        patch_scope: object,
        *,
        cwd: str | Path | None = None,
        environment_id: object = "",
    ) -> None:
        """将补丁中的每个文件加入本会话批准集合。"""
        keys = self._patch_approval_keys(
            patch_scope,
            cwd=cwd,
            environment_id=environment_id,
        )
        if not keys:
            return None
        with self._write_lock:
            self._session_patch_approvals.update(keys)

    def execpolicy_command_prefix(
        self,
        command: Sequence[str] | str,
    ) -> tuple[str, ...] | None:
        """为单条简单命令生成可持久化的规则前缀。"""
        commands = commands_for_exec_policy(command)
        if len(commands) != 1 or not commands[0]:
            return None

        words = tuple(str(word) for word in commands[0] if str(word))
        head = _basename(words[0]) if words else ""

        if head in {"python", "python3", "py"} and len(words) >= 4:
            if words[1].casefold() == "-m":
                return words[:4]
        return words[:2] if len(words) >= 2 else words[:1] or None

    def proposed_execpolicy_amendment(
        self,
        command: Sequence[str] | str,
        *,
        amendment_id: str,
    ) -> dict[str, object] | None:
        """构造由客户端生成并保存的命令前缀规则提案。"""
        prefix = self.execpolicy_command_prefix(command)
        identity = str(amendment_id or "").strip()

        if prefix is None or not identity:
            return None

        return {
            "id": identity,
            "command_prefix": list(prefix),
            "display": shlex.join(prefix),
        }

    def persist_execpolicy_amendment(
        self,
        amendment: dict[str, object],
    ) -> Path:
        """校验规则提案并把允许前缀写入本地规则文件。"""
        raw_prefix = amendment.get("command_prefix")
        if not isinstance(raw_prefix, list) or not raw_prefix:
            raise ValueError("exec policy amendment command prefix is required")
        if any(not isinstance(value, str) or not value for value in raw_prefix):
            raise ValueError("exec policy amendment command prefix is invalid")

        prefix = tuple(raw_prefix)

        rule = PrefixRule(
            pattern=PrefixPattern.from_values(prefix),
            decision=Decision.Allow,
            source=str(self.writable_rules_path),
        )
        with self._write_lock:
            if not self._has_allow_prefix(prefix):
                target = self.writable_rules_path
                target.parent.mkdir(parents=True, exist_ok=True)
                source = target.read_text(encoding=const.CHARSET) if target.is_file() else ""
                if source and not source.endswith("\n"):
                    source += "\n"
                encoded_prefix = json.dumps(list(prefix), ensure_ascii=False)
                source += f'prefix_rule(pattern={encoded_prefix}, decision="allow")\n'
                target.write_text(source, encoding=const.CHARSET)
                self.policy.add_prefix_rule(rule)
        return self.writable_rules_path

    def persist_network_rule(self, rule: NetworkRulePort) -> Path:
        """校验并持久化用户确认的网络允许规则。"""
        host = str(rule.host or "").strip()
        if not host:
            raise ValueError("network rule host is required")
        raw_protocol = getattr(rule.protocol, "value", rule.protocol)
        protocol = str(raw_protocol or "").strip().casefold()
        if protocol not in {
            "http",
            "https",
            "socks5_tcp",
            "socks5_udp",
        }:
            raise ValueError("network rule protocol is invalid")
        with self._write_lock:
            already_allowed = any(
                str(existing.host).strip().casefold().rstrip(".")
                == host.casefold().rstrip(".")
                and str(getattr(existing.protocol, "value", existing.protocol))
                .strip()
                .casefold()
                == protocol
                and existing.decision is Decision.Allow
                for existing in self.policy.network_rules
            )
            if not already_allowed:
                target = self.writable_rules_path
                target.parent.mkdir(parents=True, exist_ok=True)
                source = target.read_text(encoding=const.CHARSET) if target.is_file() else ""
                if source and not source.endswith("\n"):
                    source += "\n"
                source += (
                    "network_rule("
                    f"host={json.dumps(host, ensure_ascii=False)}, "
                    f"protocol={json.dumps(protocol)}, decision=\"allow\")\n"
                )
                target.write_text(source, encoding=const.CHARSET)
                self.policy.add_network_rule(NetworkRule(
                    host=host,
                    protocol=NetworkRuleProtocol.parse(protocol),
                    decision=Decision.Allow,
                    source=str(target),
                ))
        return self.writable_rules_path

    def check(
        self,
        command: Sequence[str] | str,
        *,
        approval_policy: str = "on-request",
        sandbox_mode: str = "workspace-write",
        cwd: str | Path | None = None,
        tool: str = "shell_command",
        sandbox_permissions: object = "use_default",
    ) -> Evaluation:
        """按策略检查命令。"""
        return self.decide(
            command,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
            cwd=cwd,
            tool=tool,
            sandbox_permissions=sandbox_permissions,
        )

    def load_exec_policy(self) -> Policy:
        """返回当前管理器加载的本地策略。"""
        return self.policy

    def load_exec_policy_with_warning(self) -> tuple[Policy, tuple[str, ...]]:
        """返回本地策略及其加载警告。"""
        return self.policy, tuple(self.warnings)

    def commands_for_exec_policy(
        self,
        command: Sequence[str] | str,
    ) -> list[list[str]]:
        """提取命令中可用于策略评估的 shell 子命令。"""
        return commands_for_exec_policy(command)

    def _proposed_amendment_for_command(
        self,
        command: Sequence[str] | str,
        *,
        amendment_id: str,
    ) -> dict[str, object] | None:
        """按首个未获显式允许的命令生成修订提案。"""
        words = _split_command(command)

        commands = commands_for_exec_policy(command)
        if not commands:
            return None
        candidate: Sequence[str] | str | None = None

        options = MatchOptions(
            resolve_host_executables=True,
            host_executable_paths=tuple(
                str(item.path) for item in self.policy.host_executables
            ),
        )
        for parsed in commands:
            evaluation = self.policy.check_with_options(parsed, options)
            if not any(
                getattr(match, "decision", None) == Decision.Allow
                for match in evaluation.matched_rules
            ):
                candidate = parsed
                break
        if candidate is None:
            return None
        if _contains_heredoc(words) and len(words) >= 3:
            identity = str(amendment_id or "").strip()
            if not identity:
                return None
            prefix = tuple(str(value) for value in words if str(value))
            return {
                "id": identity,
                "command_prefix": list(prefix),
                "display": shlex.join(prefix),
            }
        return self.proposed_execpolicy_amendment(
            candidate,
            amendment_id=amendment_id,
        )

    def _all_commands_explicitly_allowed(
        self,
        command: Sequence[str] | str,
    ) -> bool:
        """判断每个解析出的命令段是否都有显式 allow 规则。"""
        commands = commands_for_exec_policy(command)
        if not commands:
            return False
        options = MatchOptions(
            resolve_host_executables=True,
            host_executable_paths=tuple(
                str(item.path) for item in self.policy.host_executables
            ),
        )
        return all(
            any(
                getattr(match, "decision", None) == Decision.Allow
                for match in self.policy.check_with_options(parsed, options).matched_rules
            )
            for parsed in commands
        )

    def _load_policy(self) -> Policy:
        policy = Policy.empty()
        for path in self.rules_paths:
            try:
                parser = PolicyParser.from_file(path)
                policy.merge_overlay(parser.build())
                self.warnings.extend(parser.warnings)
            except (OSError, UnicodeError) as error:
                self.warnings.append(f"{path}: {error}")
        return policy

    def _discover_rules_paths(self) -> tuple[Path, ...]:
        directories: list[Path] = []
        seen_directories: set[Path] = set()

        def add_directory(directory_path: Path) -> None:
            target = directory_path.expanduser().resolve()
            if target not in seen_directories and target.is_dir():
                seen_directories.add(target)
                directories.append(target)

        mind_home = os.environ.get("MIND_HOME")
        if mind_home:
            add_directory(Path(mind_home) / "rules")
        add_directory(default_application_home() / "rules")

        ancestors = [self.workspace_root, *self.workspace_root.parents]
        for ancestor in reversed(ancestors):
            add_directory(ancestor / f".{const.APP_NAME}" / "rules")

        requirement_value = (
            os.environ.get("MIND_REQUIREMENTS_RULES")
            or ""
        )
        requirement_files: list[Path] = []
        for raw_path in requirement_value.split(os.pathsep):
            if raw_path.strip():
                requirement_path = Path(raw_path).expanduser()
                if requirement_path.is_file():
                    requirement_files.append(requirement_path.resolve())
                    continue
                add_directory(requirement_path)
        discovered = self._rule_files(directories)
        for requirement_path in requirement_files:
            if requirement_path not in discovered:
                discovered.append(requirement_path)
        return tuple(discovered)

    def _session_key(
        self,
        command: Sequence[str] | str,
        *,
        tool: str,
        cwd: str | Path | None,
        sandbox_permissions: object = "use_default",
        environment_id: object = "",
        tty: object = False,
        additional_permissions: object = None,
        policy_fingerprint: object = None,
        patch_scope: object = None,
    ) -> ExecApprovalCacheKey:
        """生成会话级精确批准使用的稳定键。"""
        raw_cwd = Path(cwd or self.workspace_root).expanduser()
        resolved_cwd = (
            raw_cwd.resolve()
            if raw_cwd.is_absolute()
            else (self.workspace_root / raw_cwd).resolve()
        )
        return ExecApprovalCacheKey(
            tool=str(tool or "shell_command").strip(),
            command=tuple(_split_command(command)),
            cwd=os.path.normcase(str(resolved_cwd)),
            environment_id=str(environment_id or "").strip(),
            tty=_normalize_bool(tty),
            sandbox_permissions=normalize_sandbox_permission(sandbox_permissions),
            additional_permissions=_stable_json(additional_permissions),
            policy_fingerprint=(
                str(policy_fingerprint).strip()
                if policy_fingerprint is not None and str(policy_fingerprint).strip()
                else self.policy_fingerprint
            ),
            patch_scope=_normalize_patch_scope(patch_scope),
        )

    def _patch_approval_keys(
        self,
        patch_scope: object,
        *,
        cwd: str | Path | None,
        environment_id: object,
    ) -> tuple[ApplyPatchApprovalCacheKey, ...]:
        """生成按文件拆分的补丁会话批准键。"""
        base = Path(cwd or self.workspace_root).expanduser()
        if not base.is_absolute():
            base = self.workspace_root / base
        try:
            base = base.resolve()
        except (OSError, RuntimeError, ValueError):
            base = Path(os.path.normpath(str(base)))

        raw_values = _normalize_patch_scope(patch_scope)
        keys: set[ApplyPatchApprovalCacheKey] = set()
        normalized_environment = str(environment_id or "").strip()
        if not normalized_environment:
            normalized_environment = os.path.normcase(str(self.workspace_root))
        for raw_path in raw_values:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = base / path
            try:
                normalized_path = os.path.normcase(str(path.resolve()))
            except (OSError, RuntimeError, ValueError):
                normalized_path = os.path.normcase(os.path.normpath(str(path)))
            if normalized_path:
                keys.add(ApplyPatchApprovalCacheKey(
                    environment_id=normalized_environment,
                    path=normalized_path,
                ))
        return tuple(sorted(keys, key=lambda key: (key.environment_id, key.path)))

    def _has_allow_prefix(self, prefix: tuple[str, ...]) -> bool:
        """判断当前策略是否已包含相同的简单允许前缀。"""
        for rule in self.policy.prefix_rules:
            if rule.decision != Decision.Allow:
                continue
            values = tuple(token.value for token in rule.pattern.tokens)
            if values == prefix:
                return True
        return False


def render_decision_for_unmatched_command(
    command: Sequence[str] | str,
    *,
    approval_policy: str = "on-request",
    sandbox_mode: str = "workspace-write",
    dangerous_command_match: DangerousCommandMatch | None = None,
    sandbox_permissions: object = "use_default",
) -> Decision:
    """按未命中规则时的危险启发式和审批模式给出决定。"""
    words = _split_command(command)
    match = dangerous_command_match
    if match is None:
        match = _dangerous_command_match(words)

    normalized_policy = str(approval_policy or "on-request").strip().casefold()
    normalized_sandbox = str(sandbox_mode or "workspace-write").strip().casefold()

    permission = normalize_sandbox_permission(sandbox_permissions)

    if match is not None:
        return Decision.Forbidden if normalized_policy == "never" else Decision.Prompt
    if normalized_policy == "never":
        return Decision.Allow
    if normalized_policy in {"untrusted", "unless-trusted", "unless_trusted"}:
        return Decision.Prompt
    if (
        normalized_policy == "on-request"
        and normalized_sandbox in {
        "read-only",
        "workspace-read",
        "workspace-write",
    }
        and permission == "require_escalated"
    ):
        return Decision.Prompt
    return Decision.Allow


def load_exec_policy(
    workspace_root: str | Path | None = None,
    *,
    rules_paths: Iterable[str | Path] | None = None
) -> Policy:
    """加载当前工作区的本地执行策略。"""
    return ExecPolicyManager(
        workspace_root=workspace_root,
        rules_paths=rules_paths,
    ).policy


def load_exec_policy_with_warning(
    workspace_root: str | Path | None = None,
    *,
    rules_paths: Iterable[str | Path] | None = None
) -> tuple[Policy, tuple[str, ...]]:
    """加载本地执行策略并返回解析警告。"""
    manager = ExecPolicyManager(
        workspace_root=workspace_root,
        rules_paths=rules_paths,
    )
    return manager.policy, tuple(manager.warnings)


def commands_for_exec_policy(command: Sequence[str] | str) -> list[list[str]]:
    """按简单命令规则提取可评估命令序列。"""
    words = _split_command(command)
    if not words:
        return []
    executable = _basename(words[0])
    if executable in {"sh", "bash", "zsh", "ksh", "dash", "fish", "cmd", "powershell", "pwsh"}:
        scripts = _shell_scripts(words[1:])
        if scripts:
            parsed = _parse_plain_script(scripts[0])
            return parsed if parsed is not None else [words]
    if isinstance(command, str):
        parsed = _parse_plain_script(command)
        return parsed if parsed is not None else [words]
    return [words]


def _has_prompt_rule(evaluation: Evaluation) -> bool:
    """判断评估是否命中了显式 prompt 规则。"""
    return any(
        getattr(match, "decision", None) == Decision.Prompt
        for match in evaluation.matched_rules
    )


def _evaluation_reason(evaluation: Evaluation, fallback: str) -> str:
    """提取规则说明作为审批或拒绝原因。"""
    for match in evaluation.matched_rules:
        justification = str(getattr(match, "justification", "") or "").strip()
        if justification:
            return justification
    return fallback


def _as_exec_policy_amendment(
    value: dict[str, object] | None,
) -> ExecutionPolicyAmendment | None:
    """把内部修订字典转换成稳定的策略提案对象。"""
    if not isinstance(value, dict):
        return None
    amendment_id = str(value.get("id") or "").strip()
    raw_prefix = value.get("command_prefix")
    if not amendment_id or not isinstance(raw_prefix, list):
        return None
    prefix = tuple(
        item for item in raw_prefix
        if isinstance(item, str) and item
    )
    if len(prefix) != len(raw_prefix):
        return None
    return ExecutionPolicyAmendment(
        id=amendment_id,
        command_prefix=prefix,
        display=str(value.get("display") or "").strip(),
    )


def _contains_heredoc(command: Sequence[str]) -> bool:
    """判断命令参数中是否包含 heredoc 重定向。"""
    return any("<<" in str(value) for value in command)


def _split_command(command: Sequence[str] | str) -> list[str]:
    if isinstance(command, str):
        try:
            return shlex.split(command, posix=os.name != "nt")
        except ValueError:
            return [part for part in command.split() if part]
    return [str(word) for word in command if str(word)]


def _parse_plain_script(script: str) -> list[list[str]] | None:
    """解析不含动态 shell 语法的简单命令链。"""
    segments = _split_plain_segments(script)
    if segments is None:
        return None

    commands: list[list[str]] = []
    for segment in segments:
        try:
            words = shlex.split(segment, posix=True)
        except ValueError:
            return None
        if not words or _is_shell_control_word(words[0]):
            return None
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            return None
        commands.append(words)
    return commands or None


def _split_plain_segments(script: str) -> list[str] | None:
    """按引号外的安全连接符切分脚本，复杂语法返回 None。"""
    text = str(script or "")
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(text):
        char = text[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue
        if char in "()<>" or char == "$" or char == "`":
            return None
        if text.startswith("&&", index) or text.startswith("||", index):
            segments.append("".join(current).strip())
            current = []
            index += 2
            continue
        if char in {";", "|"}:
            segments.append("".join(current).strip())
            current = []
            index += 1
            continue
        if char == "&":
            return None
        current.append(char)
        index += 1

    if quote is not None or escaped:
        return None
    segments.append("".join(current).strip())
    if any(not segment for segment in segments):
        return None
    return segments


def _is_shell_control_word(value: str) -> bool:
    """判断词元是否为需要完整语法分析的 shell 控制词。"""
    return str(value or "").casefold() in {
        "if", "then", "elif", "else", "fi",
        "for", "while", "until", "do", "done",
        "case", "esac", "select", "function",
    }


def _shell_scripts(args: Sequence[str]) -> tuple[str, ...]:
    switches = {"-c", "-lc", "-ic", "--command", "/c"}
    return tuple(
        str(args[index + 1])
        for index, item in enumerate(args[:-1])
        if str(item).casefold() in switches
    )


def _basename(value: str) -> str:
    text = str(value or "").strip().strip('"\'').replace("\\", "/")
    return Path(text).name.casefold().removesuffix(".exe")


if __name__ == '__main__':
    pass
