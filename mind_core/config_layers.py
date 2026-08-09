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
from mind_nova import const

ConfigScope = typing.Literal["user", "profile", "project", "cli"]

ProjectTrustLevel = typing.Literal["trusted", "untrusted"]

PROFILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

PROJECT_CONFIG_DIR = f".{const.APP_NAME}"

PROJECT_RESTRICTED_ROOTS = frozenset({
    "model_provider",
    "model_providers",
    "project_root_markers",
    "projects",
    "service",
    "sandbox_mode",
    "approval_policy",
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
    """描述一个已经参与解析的配置来源。"""
    scope: ConfigScope
    path: Path | None


@dataclass(frozen=True, slots=True)
class ProjectTrust(object):
    """描述项目配置根、信任登记根及其用户决定。"""
    project_root: Path
    trust_root: Path
    level: ProjectTrustLevel | None

    @property
    def trusted(self) -> bool:
        """返回项目是否已被明确标记为可信。"""
        return self.level == "trusted"


@dataclass(frozen=True, slots=True)
class ConfigResolution(object):
    """保存有效配置及其来源信息。"""
    config: dict[str, typing.Any]
    layers: tuple[ConfigLayer, ...]
    hooks: tuple[HookDefinitionConfig, ...] = ()
    hook_states: HookStateTable = field(default_factory=dict)
    hook_warnings: tuple[str, ...] = ()
    project_trust: ProjectTrust | None = None


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

        hook_warnings: list[str] = []

        effective_workspace = (
            Path(workspace).expanduser().resolve()
            if workspace is not None
            else self.workspace
        )

        layers: list[ConfigLayer] = [ConfigLayer("user", self.store.path)]

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

            layers.append(ConfigLayer("profile", profile_store.path))

            hooks.extend(resolve_hook_definitions(
                profile.get("hooks"),
                source_scope="profile",
                source_path=profile_store.path,
                warnings=hook_warnings,
            ))

        project_trust: ProjectTrust | None = None

        if effective_workspace is not None:
            project_root = _find_project_root(
                effective_workspace,
                _project_root_markers(merged),
            )

            project_trust = _resolve_project_trust(
                user,
                project_root,
                effective_workspace,
            )
            for directory in _project_config_directories(
                project_root,
                effective_workspace,
            ):
                path = directory / PROJECT_CONFIG_DIR / "config.toml"
                if _path_key(path) == _path_key(self.store.path):
                    continue
                if not path.is_file():
                    continue
                if not _project_directory_trusted(
                    user,
                    directory,
                    project_trust,
                ):
                    continue

                project_config = _read_config(
                    ConfigStore(path),
                    create=False,
                )
                _validate_project_config(project_config, path)

                merged = _merge_config(merged, project_config)

                layers.append(ConfigLayer("project", path))

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


def _find_project_root(workspace: Path, markers: tuple[str, ...]) -> Path:
    """从工作目录向上查找最近的项目根。"""
    if not markers:
        return workspace

    for candidate in (workspace, *workspace.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate

    return workspace


def _resolve_project_trust(
    user_config: dict[str, typing.Any],
    project_root: Path,
    workspace: Path
) -> ProjectTrust:
    """解析项目配置根对应的信任登记位置和已有决定。"""
    checkout_root = _find_git_checkout_root(workspace)

    default_trust_root = (
        _resolve_git_trust_root(checkout_root)
        if checkout_root is not None
        else project_root
    )

    decision_roots = (
        (project_root,)
        if project_root == default_trust_root
        else (project_root, default_trust_root)
    )

    for trust_root in decision_roots:
        level = _project_trust_level(user_config, trust_root)
        if level is not None:
            return ProjectTrust(project_root, trust_root, level)

    return ProjectTrust(project_root, default_trust_root, None)


def _find_git_checkout_root(workspace: Path) -> Path | None:
    """返回工作目录所属的最近 Git checkout 根。"""
    for candidate in (workspace, *workspace.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _resolve_git_trust_root(checkout_root: Path) -> Path:
    """通过 linked worktree 指针解析用于信任登记的主仓库根。"""
    dot_git = checkout_root / ".git"
    if dot_git.is_dir():
        return checkout_root

    try:
        pointer = dot_git.read_text(encoding=const.CHARSET).strip()
    except (OSError, UnicodeError):
        return checkout_root

    if "\n" in pointer or "\r" in pointer or not pointer.startswith("gitdir:"):
        return checkout_root

    raw_git_dir = pointer[len("gitdir:"):].strip()
    if not raw_git_dir:
        return checkout_root

    try:
        git_dir = Path(raw_git_dir)
        if not git_dir.is_absolute():
            git_dir = checkout_root / git_dir
        git_dir = git_dir.resolve()
    except (OSError, RuntimeError, ValueError):
        return checkout_root

    worktrees_dir = git_dir.parent
    common_dir    = worktrees_dir.parent

    if worktrees_dir.name != "worktrees" or common_dir.name != ".git":
        return checkout_root

    return common_dir.parent


def _project_trust_level(
    user_config: dict[str, typing.Any],
    project_root: Path
) -> ProjectTrustLevel | None:
    """返回用户对项目根保存的信任决定。"""
    projects = user_config.get("projects")
    if not isinstance(projects, dict):
        return None

    root_key = _path_key(project_root)

    for raw_path, value in projects.items():
        if not isinstance(value, dict):
            continue
        try:
            configured = Path(str(raw_path)).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if _path_key(configured) == root_key:
            level = value.get("trust_level")
            if level in {"trusted", "untrusted"}:
                return typing.cast(ProjectTrustLevel, level)
            return None

    return None


def _project_directory_trusted(
    user_config: dict[str, typing.Any],
    directory: Path,
    project_trust: ProjectTrust,
) -> bool:
    """返回项目配置目录自己的决定或继承后的信任状态。"""
    level = _project_trust_level(user_config, directory)
    if level is None:
        return project_trust.trusted
    return level == "trusted"


def _path_key(path: Path) -> str:
    """返回适合当前平台比较的绝对路径键。"""
    return os.path.normcase(str(path.resolve()))


def _project_config_directories(
    project_root: Path,
    workspace: Path
) -> tuple[Path, ...]:
    """返回从项目根到工作目录的配置目录。"""
    try:
        relative = workspace.relative_to(project_root)
    except ValueError:
        return ()

    directories = [project_root]
    current     = project_root

    for component in relative.parts:
        current /= component
        directories.append(current)
    return tuple(directories)


def _validate_project_config(
    config: dict[str, typing.Any],
    path: Path
) -> None:
    """拒绝项目层覆盖机器级配置。"""
    blocked = sorted(PROJECT_RESTRICTED_ROOTS.intersection(config))
    if blocked:
        raise ValueError(
            f"project config cannot override {', '.join(blocked)}: {path}"
        )


if __name__ == "__main__":
    pass
