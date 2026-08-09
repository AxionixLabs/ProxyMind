# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import copy
import typing
from dataclasses import (
    dataclass,
    field
)
from pathlib import Path
from mind_core.config import (
    ConfigOverride,
    apply_config_overrides,
    normalize_config,
    validate_config
)
from mind_core.config_store import (
    ConfigStore,
    ConfigStoreError
)
from mind_core.hook_discovery import (
    normalize_hook_table,
    resolve_hook_definitions
)
from mind_core.hooks import (
    HookDefinitionConfig,
    HookStateTable
)
from mind_core.project_trust import (
    ProjectTrustContext,
    ProjectTrustDecision
)
from mind_nova import const

ConfigScope = typing.Literal["user", "profile", "project", "cli"]

PROFILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

PROJECT_CONFIG_DIR = f".{const.APP_NAME}"

PROJECT_USER_ONLY_ROOTS = frozenset({
    "model_provider",
    "model_providers",
    "project_root_markers",
    "projects",
    "service",
    "tui",
})

MCP_STDIO_FIELDS = frozenset({
    "command",
    "args",
    "env",
    "cwd"
})

MCP_REMOTE_FIELDS = frozenset({
    "url",
    "bearer_token_env_var",
    "http_headers",
    "env_http_headers",
})


@dataclass(frozen=True, slots=True)
class ConfigLayer(object):
    """描述一个已发现配置来源及其启用状态。"""
    scope: ConfigScope
    path: Path | None
    enabled: bool = True
    disabled_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigResolution(object):
    """保存有效配置及其来源信息。"""
    config: dict[str, typing.Any]
    layers: tuple[ConfigLayer, ...]
    hooks: tuple[HookDefinitionConfig, ...] = ()
    hook_states: HookStateTable = field(default_factory=dict)
    hook_warnings: tuple[str, ...] = ()
    startup_warnings: tuple[str, ...] = ()
    project_trust: ProjectTrustDecision | None = None


class ConfigResolver(object):
    """按照稳定优先级解析用户、Profile、项目和 CLI 配置。"""

    def __init__(
        self,
        store: ConfigStore,
        overrides: tuple[ConfigOverride, ...] = (),
        *,
        profile: str | None = None,
        workspace: Path | None = None
    ) -> None:
        self.store     = store
        self.overrides = tuple(overrides)
        self.profile   = normalize_profile_name(profile)

        self.workspace = (
            Path(workspace).expanduser().resolve()
            if workspace is not None
            else None
        )

    def resolve(
        self,
        *,
        create: bool = True,
        workspace: Path | None = None
    ) -> ConfigResolution:
        """解析配置层并返回有效配置。"""
        user = _read_config(self.store, create=create)
        return self.resolve_user_config(user, workspace=workspace)

    def resolve_user_config(
        self,
        user: dict[str, typing.Any],
        *,
        workspace: Path | None = None
    ) -> ConfigResolution:
        """基于候选用户配置解析全部配置层。"""
        validate_config(user)

        merged      = copy.deepcopy(user)
        hook_states = _effective_hook_states(user, self.overrides)

        hook_warnings: list[str]    = []
        startup_warnings: list[str] = []

        effective_workspace = (
            Path(workspace).expanduser().resolve()
            if workspace is not None
            else self.workspace
        )

        layers: list[ConfigLayer] = [ConfigLayer("user", self.store.path)]

        active_user_config_file = self.store.path

        hooks: list[HookDefinitionConfig] = list(resolve_hook_definitions(
            user.get("hooks"),
            source_scope="user",
            source_path=self.store.path,
            warnings=hook_warnings,
        ))

        if self.profile is not None:
            profile_store = ConfigStore(
                self.store.path.parent / f"{self.profile}.config.toml"
            )
            try:
                profile = _read_config(
                    profile_store,
                    create=False,
                )
            except ConfigStoreError as error:
                raise ConfigStoreError(
                    f"config profile is unavailable: {self.profile}"
                ) from error

            merged = _merge_config(merged, profile)

            active_user_config_file = profile_store.path

            layers.append(ConfigLayer("profile", profile_store.path))

            hooks.extend(resolve_hook_definitions(
                profile.get("hooks"),
                source_scope="profile",
                source_path=profile_store.path,
                warnings=hook_warnings,
            ))

        project_trust: ProjectTrustDecision | None = None

        if effective_workspace is not None:
            project_context_config = _project_context_config(
                merged,
                self.overrides,
            )
            trust_context = ProjectTrustContext.resolve(
                project_context_config,
                workspace=effective_workspace,
                project_root_markers=_project_root_markers(
                    project_context_config
                ),
                user_config_file=active_user_config_file,
            )

            project_trust = trust_context.active_decision

            for directory in trust_context.config_directories():
                path = directory / PROJECT_CONFIG_DIR / "config.toml"
                if _path_key(path) == _path_key(self.store.path):
                    continue
                if not path.is_file():
                    continue

                decision        = trust_context.decision_for_directory(directory)
                disabled_reason = trust_context.disabled_reason(decision)

                layers.append(ConfigLayer(
                    "project",
                    path,
                    enabled=decision.trusted,
                    disabled_reason=disabled_reason,
                ))
                if not decision.trusted:
                    continue

                project_config, ignored = _read_project_config(path)

                if ignored:
                    startup_warnings.append(
                        _project_ignored_config_keys_warning(path, ignored)
                    )

                merged = _merge_config(merged, project_config)

                hooks.extend(resolve_hook_definitions(
                    project_config.get("hooks"),
                    source_scope="project",
                    source_path=path,
                    warnings=hook_warnings,
                ))

        if self.overrides:
            merged = apply_config_overrides(merged, self.overrides)
            if any(override.path == ("hooks",) for override in self.overrides):
                hook_warnings.clear()
                hooks = list(resolve_hook_definitions(
                    merged.get("hooks"),
                    source_scope="cli",
                    source_path=None,
                    warnings=hook_warnings,
                ))
            layers.append(ConfigLayer("cli", None))

        config = normalize_config(merged)

        if hook_states:
            config["hooks"]["state"] = copy.deepcopy(hook_states)
        else:
            config["hooks"].pop("state", None)

        return ConfigResolution(
            config=config,
            layers=tuple(layers),
            hooks=tuple(hooks),
            hook_states=hook_states,
            hook_warnings=tuple(hook_warnings),
            startup_warnings=tuple(startup_warnings),
            project_trust=project_trust,
        )


def _effective_hook_states(
    user: dict[str, typing.Any],
    overrides: tuple[ConfigOverride, ...]
) -> HookStateTable:
    """合并用户配置与当前进程覆盖中的 Hook 状态。"""
    user_hooks = user.get("hooks")

    raw_state = (
        user_hooks.get("state", {})
        if isinstance(user_hooks, dict)
        else {}
    )

    state_document: dict[str, typing.Any] = {
        "hooks": {"state": copy.deepcopy(raw_state)},
    }

    state_overrides = tuple(
        override
        for override in overrides
        if (
            override.path == ("hooks",)
            or override.path[:2] == ("hooks", "state")
        )
    )

    if state_overrides:
        state_document = apply_config_overrides(
            state_document,
            state_overrides,
        )

    hooks = state_document.get("hooks")

    normalized = normalize_hook_table(
        hooks if isinstance(hooks, dict) else {}
    )

    return dict(normalized.get("state", {}))


def normalize_profile_name(value: str | None) -> str | None:
    """校验可用于 Profile 文件名的名称。"""
    if value is None:
        return None

    name = str(value).strip()

    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "profile name may contain only letters, numbers, hyphens, and underscores"
        )

    return name


def _read_config(
    store: ConfigStore,
    *,
    create: bool
) -> dict[str, typing.Any]:
    """读取并校验一个配置文档。"""
    raw = store.read_raw(create=create)
    validate_config(raw)
    return raw


def _merge_config(
    base: dict[str, typing.Any],
    overlay: dict[str, typing.Any],
    path: tuple[str, ...] = ()
) -> dict[str, typing.Any]:
    """递归合并配置表，标量和列表由高优先级层替换。"""
    result = copy.deepcopy(base)

    for key, value in overlay.items():
        child_path = (*path, key)
        current    = result.get(key)

        if path == ("hooks",) and isinstance(current, list) and isinstance(value, list):
            result[key] = [*copy.deepcopy(current), *copy.deepcopy(value)]
        elif isinstance(current, dict) and isinstance(value, dict):
            if len(child_path) == 2 and child_path[0] == "mcp_servers":
                current = _mcp_transport_base(current, value)
            result[key] = _merge_config(current, value, child_path)
        else:
            result[key] = copy.deepcopy(value)

    return result


def _mcp_transport_base(
    base: dict[str, typing.Any],
    overlay: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """切换 MCP 传输目标时清除另一类传输字段。"""
    command = str(overlay.get("command") or "").strip()
    url     = str(overlay.get("url") or "").strip()

    if command and not url:
        removed = MCP_REMOTE_FIELDS
    elif url and not command:
        removed = MCP_STDIO_FIELDS
    else:
        return base

    return {
        key: copy.deepcopy(value)
        for key, value in base.items()
        if key not in removed
    }


def _project_root_markers(config: dict[str, typing.Any]) -> tuple[str, ...]:
    """读取项目根标记，空列表表示只使用当前目录。"""
    raw = config.get("project_root_markers", [".git"])
    if not isinstance(raw, list):
        return (".git",)

    return tuple(
        marker
        for value in raw
        if (marker := str(value or "").strip())
    )


def _project_context_config(
    config: dict[str, typing.Any],
    overrides: tuple[ConfigOverride, ...]
) -> dict[str, typing.Any]:
    """构造只影响项目发现与信任判断的启动配置快照。"""
    context_overrides = tuple(
        override
        for override in overrides
        if (
            override.path
            and override.path[0] in {"project_root_markers", "projects"}
        )
    )
    if not context_overrides:
        return config
    return apply_config_overrides(config, context_overrides)


def _path_key(path: Path) -> str:
    """返回适合当前平台比较的绝对路径键。"""
    return os.path.normcase(str(path.expanduser().resolve()))


def _read_project_config(
    path: Path
) -> tuple[dict[str, typing.Any], tuple[str, ...]]:
    """读取项目配置并移除只能由用户层设置的根字段。"""
    config = typing.cast(
        dict[str, typing.Any],
        ConfigStore(path).read_raw(create=False),
    )

    ignored = tuple(sorted(PROJECT_USER_ONLY_ROOTS.intersection(config)))
    for key in ignored:
        config.pop(key, None)
    validate_config(config)

    return config, ignored


def _project_ignored_config_keys_warning(
    path: Path,
    ignored: tuple[str, ...]
) -> str:
    """生成项目层受限字段被忽略时的启动告警。"""
    return (
        f"Ignored unsupported project-local config keys in {path}: "
        f"{', '.join(ignored)}. Configure these settings in the user-level "
        "config.toml instead."
    )


if __name__ == "__main__":
    pass
