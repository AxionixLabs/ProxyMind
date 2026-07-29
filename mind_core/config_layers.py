# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import copy
import typing
from dataclasses import dataclass
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
from mind_core.hooks import (
    HookDefinitionConfig,
    resolve_hook_definitions
)
from mind_nova import const

ConfigScope = typing.Literal["user", "profile", "project", "cli"]

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
class ConfigResolution(object):
    """保存有效配置及其来源信息。"""
    config: dict[str, typing.Any]
    layers: tuple[ConfigLayer, ...]
    hooks: tuple[HookDefinitionConfig, ...] = ()
    project_root: Path | None = None
    project_trusted: bool = False


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

    def set_workspace(self, workspace: Path | None) -> None:
        """更新后续配置解析使用的工作目录。"""
        self.workspace = (
            Path(workspace).expanduser().resolve()
            if workspace is not None
            else None
        )

    def resolve(self, *, create: bool = True) -> ConfigResolution:
        """解析配置层并返回有效配置。"""
        user = _read_config(self.store, create=create)
        return self.resolve_user_config(user)

    def resolve_user_config(
        self,
        user: dict[str, typing.Any],
    ) -> ConfigResolution:
        """基于候选用户配置解析全部配置层。"""
        validate_config(user)
        merged = copy.deepcopy(user)

        layers: list[ConfigLayer] = [ConfigLayer("user", self.store.path)]

        hooks: list[HookDefinitionConfig] = list(resolve_hook_definitions(
            user.get("hooks"),
            source_scope="user",
            source_path=self.store.path,
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
            ))

        project_root: Path | None = None
        project_trusted: bool     = False

        if self.workspace is not None:
            project_root = _find_project_root(
                self.workspace,
                _project_root_markers(merged),
            )
            project_trusted = _project_is_trusted(user, project_root)
            if project_trusted:
                for path in _project_config_paths(
                    project_root,
                    self.workspace,
                ):
                    if not path.is_file():
                        continue

                    project = _read_config(
                        ConfigStore(path),
                        create=False,
                    )
                    _validate_project_config(project, path)

                    merged = _merge_config(merged, project)

                    layers.append(ConfigLayer("project", path))

                    hooks.extend(resolve_hook_definitions(
                        project.get("hooks"),
                        source_scope="project",
                        source_path=path,
                    ))

        if self.overrides:
            merged = apply_config_overrides(merged, self.overrides)
            if any(override.path == ("hooks",) for override in self.overrides):
                hooks = list(resolve_hook_definitions(
                    merged.get("hooks"),
                    source_scope="cli",
                    source_path=None,
                ))
            layers.append(ConfigLayer("cli", None))

        return ConfigResolution(
            config=normalize_config(merged),
            layers=tuple(layers),
            hooks=tuple(hooks),
            project_root=project_root,
            project_trusted=project_trusted,
        )


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
    create: bool,
) -> dict[str, typing.Any]:
    """读取并校验一个配置文档。"""
    raw = store.read_raw(create=create)
    validate_config(raw)
    return raw


def _merge_config(
    base: dict[str, typing.Any],
    overlay: dict[str, typing.Any],
    path: tuple[str, ...] = (),
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
    overlay: dict[str, typing.Any],
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


def _project_is_trusted(
    user_config: dict[str, typing.Any],
    project_root: Path,
) -> bool:
    """判断用户配置是否把项目根标记为可信。"""
    projects = user_config.get("projects")
    if not isinstance(projects, dict):
        return False

    root_key = _path_key(project_root)

    for raw_path, value in projects.items():
        if not isinstance(value, dict):
            continue
        try:
            configured = Path(str(raw_path)).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if _path_key(configured) == root_key:
            return value.get("trust_level") == "trusted"

    return False


def _path_key(path: Path) -> str:
    """返回适合当前平台比较的绝对路径键。"""
    return os.path.normcase(str(path.resolve()))


def _project_config_paths(
    project_root: Path,
    workspace: Path,
) -> tuple[Path, ...]:
    """返回从项目根到工作目录的项目配置路径。"""
    try:
        relative = workspace.relative_to(project_root)
    except ValueError:
        return ()

    directories = [project_root]
    current     = project_root

    for component in relative.parts:
        current /= component
        directories.append(current)
    return tuple(
        directory / PROJECT_CONFIG_DIR / "config.toml"
        for directory in directories
    )


def _validate_project_config(
    config: dict[str, typing.Any],
    path: Path,
) -> None:
    """拒绝项目层覆盖机器级配置。"""
    blocked = sorted(PROJECT_RESTRICTED_ROOTS.intersection(config))
    if blocked:
        raise ValueError(
            f"project config cannot override {', '.join(blocked)}: {path}"
        )


if __name__ == "__main__":
    pass
