# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from dataclasses import dataclass
from pathlib import Path

from metadata import const

ProjectTrustLevel = typing.Literal[
    "trusted",
    "untrusted"
]

ProjectTrustSource = typing.Literal[
    "directory",
    "project_root",
    "repository_root",
    "default",
]


@dataclass(frozen=True, slots=True)
class ProjectTrustDecision(object):
    """描述一个目录最终采用的项目信任决定。"""
    project_root: Path
    directory: Path
    trust_root: Path
    registry_key: str
    level: ProjectTrustLevel | None
    source: ProjectTrustSource

    @property
    def trusted(self) -> bool:
        """返回目录是否已被明确标记为可信。"""
        return self.level == "trusted"


@dataclass(frozen=True, slots=True)
class _ProjectTrustRecord(object):
    """保存一个已规范化的项目信任登记项。"""
    key: str
    lookup_key: str
    level: ProjectTrustLevel


@dataclass(frozen=True, slots=True)
class ProjectTrustContext(object):
    """集中保存项目边界、仓库边界和目录信任登记。"""
    workspace: Path
    project_root: Path
    checkout_root: Path | None
    repository_root: Path | None
    user_config_file: Path
    _records: tuple[_ProjectTrustRecord, ...]

    @classmethod
    def resolve(
        cls,
        user_config: dict[str, typing.Any],
        *,
        workspace: Path,
        project_root_markers: tuple[str, ...],
        user_config_file: Path,
    ) -> "ProjectTrustContext":
        """从工作目录和用户登记表解析完整信任上下文。"""
        resolved_workspace = Path(workspace).expanduser().resolve()

        project_root = _find_project_root(
            resolved_workspace,
            project_root_markers,
        )

        checkout_root = _find_git_checkout_root(resolved_workspace)

        repository_root = (
            _resolve_git_repository_root(checkout_root)
            if checkout_root is not None
            else None
        )

        return cls(
            workspace=resolved_workspace,
            project_root=project_root,
            checkout_root=checkout_root,
            repository_root=repository_root,
            user_config_file=Path(user_config_file).expanduser().resolve(),
            _records=_project_trust_records(user_config),
        )

    @property
    def active_decision(self) -> ProjectTrustDecision:
        """返回当前工作目录采用的信任决定。"""
        candidates: list[tuple[ProjectTrustSource, Path]] = [
            ("directory", self.workspace),
        ]
        if self.repository_root is not None:
            candidates.append(("repository_root", self.repository_root))

        return self._decision(
            self.workspace,
            candidates,
            default_root=self.repository_root or self.workspace,
        )

    def decision_for_directory(self, directory: Path) -> ProjectTrustDecision:
        """按目录、项目根和主仓库根的顺序解析信任决定。"""
        resolved_directory = Path(directory).expanduser().resolve()

        candidates: list[tuple[ProjectTrustSource, Path]] = [
            ("directory", resolved_directory),
            ("project_root", self.project_root),
        ]

        if self.repository_root is not None:
            candidates.append(("repository_root", self.repository_root))

        return self._decision(
            resolved_directory,
            candidates,
            default_root=self.repository_root or self.project_root,
        )

    def _decision(
        self,
        directory: Path,
        candidates: list[tuple[ProjectTrustSource, Path]],
        *,
        default_root: Path
    ) -> ProjectTrustDecision:
        """按候选顺序返回首个登记决定或指定默认目标。"""
        visited: set[str] = set()
        for source, candidate in candidates:
            candidate_key = _path_key(candidate)
            if candidate_key in visited:
                continue
            visited.add(candidate_key)

            record = _record_for_path(self._records, candidate)
            if record is not None:
                return ProjectTrustDecision(
                    project_root=self.project_root,
                    directory=directory,
                    trust_root=candidate,
                    registry_key=record.key,
                    level=record.level,
                    source=source,
                )

        return ProjectTrustDecision(
            project_root=self.project_root,
            directory=directory,
            trust_root=default_root,
            registry_key=str(default_root),
            level=None,
            source="default",
        )

    def config_directories(self) -> tuple[Path, ...]:
        """返回从项目根到当前工作目录的配置目录。"""
        try:
            relative = self.workspace.relative_to(self.project_root)
        except ValueError:
            return ()

        directories = [self.project_root]
        current = self.project_root

        for component in relative.parts:
            current /= component
            directories.append(current)

        return tuple(directories)

    def root_checkout_path_for(self, path: Path) -> Path | None:
        """返回 linked worktree 路径在主 checkout 中的对应位置。"""
        if (
            self.checkout_root is None
            or self.repository_root is None
            or self.checkout_root == self.repository_root
        ):
            return None

        try:
            relative = Path(path).relative_to(self.checkout_root)
        except ValueError:
            return None

        return self.repository_root / relative

    def disabled_reason(self, decision: ProjectTrustDecision) -> str | None:
        """返回未启用项目层的可操作诊断信息。"""
        if decision.trusted:
            return None

        features = "project-local config, hooks, and exec policies"

        if decision.level == "untrusted":
            return (
                f"{decision.registry_key} is marked as untrusted in "
                f"{self.user_config_file}. To load {features}, mark it trusted."
            )

        return (
            f"To load {features}, add {decision.registry_key} as a trusted "
            f"project in {self.user_config_file}."
        )


def _find_project_root(workspace: Path, markers: tuple[str, ...]) -> Path:
    """从工作目录向上查找最近的项目根。"""
    if not markers:
        return workspace

    for candidate in (workspace, *workspace.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate

    return workspace


def _find_git_checkout_root(workspace: Path) -> Path | None:
    """返回工作目录所属的最近 Git checkout 根。"""
    for candidate in (workspace, *workspace.parents):
        if (candidate / ".git").exists():
            return candidate

    return None


def _resolve_git_repository_root(checkout_root: Path) -> Path | None:
    """解析普通 checkout 或 linked worktree 对应的主仓库根。"""
    dot_git = checkout_root / ".git"
    if dot_git.is_dir():
        return checkout_root

    try:
        pointer = dot_git.read_text(encoding=const.CHARSET).strip()
    except (OSError, UnicodeError):
        return None

    if "\n" in pointer or "\r" in pointer or not pointer.startswith("gitdir:"):
        return None

    raw_git_dir = pointer[len("gitdir:"):].strip()
    if not raw_git_dir:
        return None

    try:
        git_dir = Path(raw_git_dir)
        if not git_dir.is_absolute():
            git_dir = checkout_root / git_dir
        git_dir = git_dir.resolve()
    except (OSError, RuntimeError, ValueError):
        return None

    worktrees_dir = git_dir.parent

    common_dir = worktrees_dir.parent
    if worktrees_dir.name != "worktrees" or common_dir.name != ".git":
        return None

    return common_dir.parent


def _project_trust_records(
    user_config: dict[str, typing.Any]
) -> tuple[_ProjectTrustRecord, ...]:
    """读取并规范化用户配置中的项目信任登记。"""
    projects = user_config.get("projects")
    if not isinstance(projects, dict):
        return ()

    records: list[_ProjectTrustRecord] = []
    for raw_path, value in projects.items():
        if not isinstance(value, dict):
            continue
        level = value.get("trust_level")
        if level == "trusted":
            normalized_level: ProjectTrustLevel = "trusted"
        elif level == "untrusted":
            normalized_level = "untrusted"
        else:
            continue
        key = str(raw_path)
        records.append(_ProjectTrustRecord(
            key=key,
            lookup_key=os.path.normcase(key),
            level=normalized_level,
        ))

    records.sort(key=lambda record: record.key)
    return tuple(records)


def _record_for_path(
    records: tuple[_ProjectTrustRecord, ...],
    path: Path
) -> _ProjectTrustRecord | None:
    """返回与目标路径登记键匹配的确定性信任项。"""
    lookup_key = os.path.normcase(str(path))
    for record in records:
        if record.lookup_key == lookup_key:
            return record
    return None


def _path_key(path: Path) -> str:
    """返回适合当前平台比较的绝对路径键。"""
    return os.path.normcase(str(path.resolve()))


if __name__ == "__main__":
    pass
