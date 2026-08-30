# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import enum
import asyncio
from dataclasses import dataclass
from pathlib import Path
from .workspace import (
    LocalWorkspaceCommandRunner,
    WorkspaceCommand,
    WorkspaceCommandError,
    WorkspaceCommandOutput,
    WorkspaceCommandRunner
)

SAFE_BARE_REPOSITORY_CONFIG      = "safe.bareRepository=explicit"
DIFF_COMMAND_TIMEOUT_SEC         = 30.0
PROBE_COMMAND_TIMEOUT_SEC        = 5.0
PROBE_OUTPUT_BYTES_CAP           = 64 * 1024
EXECUTABLE_FILTER_CONFIG_PATTERN = r"^filter\..*\.(clean|process)$"


class WorkspaceDiffState(enum.Enum):
    """描述 Git 工作区差异查询的业务状态。"""
    READY = "ready"
    NOT_GIT_REPOSITORY = "not_git_repository"


@dataclass(frozen=True, slots=True)
class WorkspaceDiffResult(object):
    """保存一次 Git 工作区差异查询结果。"""
    state: WorkspaceDiffState
    text: str = ""


class WorkspaceDiffError(RuntimeError):
    """表示 Git 工作区差异无法完整计算。"""


class FsmonitorOverride(enum.Enum):
    """描述内部 Git 命令允许使用的文件监视策略。"""
    DISABLED = "core.fsmonitor=false"
    BUILT_IN = "core.fsmonitor=true"


class WorkspaceDiffService(object):
    """计算工作区 tracked 与 untracked Git 差异。"""

    def __init__(self, runner: WorkspaceCommandRunner | None = None) -> None:
        """绑定工作区命令执行方。"""
        self._runner = runner or LocalWorkspaceCommandRunner()

    async def compute(
        self,
        cwd: str | os.PathLike[str]
    ) -> WorkspaceDiffResult:
        """返回指定目录的完整工作区差异。"""
        workdir = Path(cwd).resolve()
        if not await self._inside_git_repository(workdir):
            return WorkspaceDiffResult(WorkspaceDiffState.NOT_GIT_REPOSITORY)

        fsmonitor = await self._detect_fsmonitor_override(workdir)

        filter_overrides = await self._diff_filter_config_overrides(
            workdir,
            fsmonitor,
        )

        parallel_tasks = (
            asyncio.create_task(self._run_diff(
                workdir,
                fsmonitor,
                filter_overrides,
                (
                    "diff",
                    "--no-textconv",
                    "--no-ext-diff",
                    "--submodule=short",
                    "--ignore-submodules=dirty",
                    "--color",
                ),
            )),
            asyncio.create_task(self._run_stdout(
                workdir,
                fsmonitor,
                (),
                ("ls-files", "--others", "--exclude-standard"),
            )),
        )
        try:
            tracked_result, untracked_result = await asyncio.gather(
                *parallel_tasks,
            )
        except (Exception, asyncio.CancelledError):
            for task in parallel_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*parallel_tasks, return_exceptions=True)
            raise

        untracked_parts: list[str] = []

        null_device = "NUL" if os.name == "nt" else "/dev/null"

        for raw_path in untracked_result.split("\n"):
            relative_path = raw_path.strip()
            if not relative_path:
                continue
            untracked_parts.append(await self._run_diff(
                workdir,
                fsmonitor,
                filter_overrides,
                (
                    "diff",
                    "--no-textconv",
                    "--no-ext-diff",
                    "--submodule=short",
                    "--ignore-submodules=dirty",
                    "--color",
                    "--no-index",
                    "--",
                    null_device,
                    relative_path,
                ),
            ))

        return WorkspaceDiffResult(
            WorkspaceDiffState.READY,
            tracked_result + "".join(untracked_parts),
        )

    async def _inside_git_repository(
        self,
        cwd: Path
    ) -> bool:
        """返回目录是否位于 Git work tree 内。"""
        output = await self._execute(
            WorkspaceCommand(
                argv=(
                    "git",
                    "-c",
                    SAFE_BARE_REPOSITORY_CONFIG,
                    "-c",
                    FsmonitorOverride.DISABLED.value,
                    "-c",
                    _disable_hooks_config(),
                    "rev-parse",
                    "--is-inside-work-tree",
                ),
                cwd=cwd,
                timeout_sec=DIFF_COMMAND_TIMEOUT_SEC,
                output_bytes_cap=None,
            )
        )
        return output.succeeded

    async def _detect_fsmonitor_override(
        self,
        cwd: Path
    ) -> FsmonitorOverride:
        """仅在 Git 明确支持时保留内建 fsmonitor daemon。"""
        raw = await self._run_probe(
            cwd,
            ("config", "--null", "--get", "core.fsmonitor"),
        )
        if raw is None or not raw.endswith("\0"):
            return FsmonitorOverride.DISABLED
        config = raw[:-1]
        if "\0" in config:
            return FsmonitorOverride.DISABLED

        normalized = config.casefold()
        if normalized in {"true", "yes", "on"}:
            configured = True
        elif normalized in {"false", "no", "off"}:
            configured = False
        else:
            typed = await self._run_probe(
                cwd,
                (
                    "config",
                    "--null",
                    "--type=bool",
                    "--fixed-value",
                    "--get",
                    "core.fsmonitor",
                    config,
                ),
            )
            configured = typed == "true\0"
        if not configured:
            return FsmonitorOverride.DISABLED

        build_options = await self._run_probe(
            cwd,
            ("version", "--build-options"),
        )
        if build_options is None:
            return FsmonitorOverride.DISABLED
        supported = any(
            line.strip() == "feature: fsmonitor--daemon"
            for line in build_options.splitlines()
        )
        return (
            FsmonitorOverride.BUILT_IN
            if supported
            else FsmonitorOverride.DISABLED
        )

    async def _run_probe(
        self,
        cwd: Path,
        args: tuple[str, ...]
    ) -> str | None:
        """执行一次安全的 Git 能力探测。"""
        try:
            output = await self._runner.run(WorkspaceCommand(
                argv=("git", "-c", SAFE_BARE_REPOSITORY_CONFIG, *args),
                cwd=cwd,
                timeout_sec=PROBE_COMMAND_TIMEOUT_SEC,
                output_bytes_cap=PROBE_OUTPUT_BYTES_CAP,
            ))
        except WorkspaceCommandError:
            return None
        return output.stdout if output.succeeded else None

    async def _diff_filter_config_overrides(
        self,
        cwd: Path,
        fsmonitor: FsmonitorOverride
    ) -> tuple[tuple[str, str], ...]:
        """返回禁用可执行 Git filter driver 的临时配置。"""
        args = (
            "config",
            "--null",
            "--name-only",
            "--get-regexp",
            EXECUTABLE_FILTER_CONFIG_PATTERN,
        )
        output = await self._run_command(cwd, fsmonitor, (), args)
        if output.exit_code not in {0, 1}:
            raise _status_error(args, output.exit_code)

        drivers = sorted({
            key.removesuffix(".clean").removesuffix(".process")
            for key in output.stdout.split("\0")
            if key.endswith((".clean", ".process"))
        })
        return tuple(
            item
            for driver in drivers
            for item in (
                (f"{driver}.clean", ""),
                (f"{driver}.process", ""),
                (f"{driver}.required", "false"),
            )
        )

    async def _run_stdout(
        self,
        cwd: Path,
        fsmonitor: FsmonitorOverride,
        config_overrides: tuple[tuple[str, str], ...],
        args: tuple[str, ...]
    ) -> str:
        """执行只接受零退出码的 Git 命令。"""
        output = await self._run_command(
            cwd,
            fsmonitor,
            config_overrides,
            args,
        )
        if not output.succeeded:
            raise _status_error(args, output.exit_code)
        return output.stdout

    async def _run_diff(
        self,
        cwd: Path,
        fsmonitor: FsmonitorOverride,
        config_overrides: tuple[tuple[str, str], ...],
        args: tuple[str, ...]
    ) -> str:
        """执行允许差异状态码一的 Git diff 命令。"""
        output = await self._run_command(
            cwd,
            fsmonitor,
            config_overrides,
            args,
        )
        if output.exit_code not in {0, 1}:
            raise _status_error(args, output.exit_code)
        return output.stdout

    async def _run_command(
        self,
        cwd: Path,
        fsmonitor: FsmonitorOverride,
        config_overrides: tuple[tuple[str, str], ...],
        args: tuple[str, ...]
    ) -> WorkspaceCommandOutput:
        """使用统一安全前缀执行 Git 命令。"""
        environment: list[tuple[str, str | None]] = []
        if config_overrides:
            environment.append(("GIT_CONFIG_COUNT", str(len(config_overrides))))
            for index, (key, value) in enumerate(config_overrides):
                environment.extend((
                    (f"GIT_CONFIG_KEY_{index}", key),
                    (f"GIT_CONFIG_VALUE_{index}", value),
                ))

        return await self._execute(WorkspaceCommand(
            argv=(
                "git",
                "-c",
                SAFE_BARE_REPOSITORY_CONFIG,
                "-c",
                fsmonitor.value,
                "-c",
                _disable_hooks_config(),
                *args,
            ),
            cwd=cwd,
            env=tuple(environment),
            timeout_sec=DIFF_COMMAND_TIMEOUT_SEC,
            output_bytes_cap=None,
        ))

    async def _execute(
        self,
        command: WorkspaceCommand
    ) -> WorkspaceCommandOutput:
        """把命令基础设施错误转换为差异领域错误。"""
        try:
            return await self._runner.run(command)
        except WorkspaceCommandError as error:
            raise WorkspaceDiffError(str(error)) from error


def _disable_hooks_config() -> str:
    """返回当前平台禁用 Git hooks 的临时配置。"""
    return "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null"


def _status_error(
    args: tuple[str, ...],
    exit_code: int
) -> WorkspaceDiffError:
    """生成包含 Git 参数和退出状态的领域错误。"""
    return WorkspaceDiffError(
        f"git {list(args)!r} failed with status {exit_code}"
    )


if __name__ == '__main__':
    pass
