# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import functools
import math
import os
import typing
from dataclasses import dataclass
from pathlib import Path

from agent.adapters.turns.root import RootTurnCommandExecutor
from agent.application import RuntimeServices
from agent.application.turns.commands import (
    SubmitTurnCommand,
    TurnApplication,
)
from agent.application.turns.projections import RunResultProjection
from agent.application.turns.run_result import RunResult
from infrastructure.config.paths import (
    ApplicationLayout,
    resolve_application_layout
)
from infrastructure.config.runtime_paths import (
    agent_runtime_db_path,
    ensure_mind_home,
    mind_config_path,
    mind_reports_dir
)
from infrastructure.platform.shell_tools import route_shell_tools
from mcp.server.fastmcp import (
    Context,
    FastMCP
)
from mind_app.controller import Mind
from mind_app.presentation.application import Frontend
from mind_app.presentation.application_sinks import NullApplicationSink
from mind_app.interaction import NonInteractiveInteraction
from mind_app.presentation.output.silent import create_silent_output_session
from observability.reporting import RunReport
from agent.application.config.settings import AgentSettings
from infrastructure.config.schema import ConfigOverride
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from agent.application.config.settings import FeatureSettings
from infrastructure.config.preferences import Preferences
from agent.domain.policies import (
    PermissionSettings,
    resolve_permissions
)
from infrastructure.services.service_config import ServiceConfig
from protocol.schema.permissions import (
    ApprovalPolicy,
    ApprovalReviewer,
    SandboxMode,
    normalize_network_access,
)
from protocol.transport.endpoints import service_endpoints
from metadata import const

DEFAULT_MCP_EXEC_TIMEOUT_SEC = 900.0


class RootTurnRunner(typing.Protocol):
    """定义组合根提供的 MCP 根轮次执行能力。"""

    async def __call__(
        self,
        controller: Mind,
        *,
        message: str,
        **kwargs: typing.Any,
    ) -> RunResult:
        """执行一次已冻结的根轮次。"""
        ...


class EnvironmentSnapshotProvider(typing.Protocol):
    """定义组合根提供的 MCP 执行环境快照能力。"""

    def __call__(
        self,
        controller: Mind,
        *,
        cwd: str | Path,
        workspace_root: str | Path,
    ) -> dict[str, typing.Any] | None:
        """捕获当前 MCP 调用使用的不可变环境快照。"""
        ...


async def _require_turn_runner(
    _controller: Mind,
    *,
    message: str,
    **_kwargs: typing.Any,
) -> RunResult:
    """在 MCP 未由组合根装配时返回明确配置错误。"""
    _ = message
    raise RuntimeError("MCP root turn runner is required")


def _require_environment_snapshot(
    _controller: Mind,
    *,
    cwd: str | Path,
    workspace_root: str | Path,
) -> dict[str, typing.Any] | None:
    """在 MCP 未由组合根装配时返回明确配置错误。"""
    _ = (cwd, workspace_root)
    raise RuntimeError("MCP environment snapshot provider is required")


@dataclass(frozen=True, slots=True)
class MindMcpExecutionResult(object):
    """描述一次 MCP 工具调用及其可续接会话。"""
    run: RunResult
    session_id: str | None = None
    projection: RunResultProjection | None = None

    def to_dict(self) -> dict[str, typing.Any]:
        """返回 MCP structured content 使用的字典。"""
        result = (
            dict(self.projection.result)
            if self.projection is not None
            else self.run.to_dict()
        )
        result["session_id"] = self.session_id
        return result


class _McpRequestState(object):
    """记录单次 MCP 请求已经绑定的会话。"""

    def __init__(self) -> None:
        self.session_id: str | None = None


class MindMcpRuntime(object):
    """管理 stdio MCP 服务持有的长驻应用运行时。"""

    def __init__(
        self,
        mind: Mind,
        *,
        report: RunReport,
        turn_runner: RootTurnRunner = _require_turn_runner,
        environment_snapshot_provider: EnvironmentSnapshotProvider = (
            _require_environment_snapshot
        ),
        turn_application: TurnApplication[RunResult],
    ) -> None:
        """绑定主控制器、主动 Turn application 和串行调用锁。"""
        self.mind              = mind
        self.default_workspace = Path(mind.history_workspace).resolve()
        self._report           = report
        self._turn_runner      = turn_runner
        self._environment_snapshot_provider = environment_snapshot_provider
        self._turn_application = turn_application

        self._call_lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        layout: ApplicationLayout,
        config_overrides: tuple[ConfigOverride, ...] = (),
        config_profile: str | None = None,
        *,
        runtime_services: RuntimeServices,
        turn_runner: RootTurnRunner = _require_turn_runner,
        environment_snapshot_provider: EnvironmentSnapshotProvider = (
            _require_environment_snapshot
        ),
    ) -> "MindMcpRuntime":
        """创建并启动 MCP 服务使用的应用运行时。"""
        home   = ensure_mind_home()
        report = RunReport(str(mind_reports_dir()), label="mcp_server")

        try:
            config_session = ConfigSession(
                ConfigStore(mind_config_path()),
                config_overrides,
                profile=config_profile,
                workspace=Path.cwd(),
            )
            config_resolution = config_session.resolve()

            permissions = resolve_permissions(
                config_resolution.config,
                interactive=False,
            )

            pref = Preferences(config_session)

            route_shell_tools(layout.supports)
            runtime_services.environment_capability.clear_cache()

            frontend = Frontend(
                application=NullApplicationSink(),
                interaction=NonInteractiveInteraction(),
                session_factory=create_silent_output_session,
            )

            hook_registry = runtime_services.create_hook_registry()

            mind = Mind(
                const.SHOW_LEVEL,
                os.cpu_count() or 1,
                {},
                src_opera_place=str(home),
                src_total_place=str(mind_reports_dir()),
                pref=pref,
                config_session=config_session,
                animate=False,
                frontend=frontend,
                design=None,
                report=report,
                workspace_root=Path.cwd(),
                permissions=permissions,
                hook_registry=hook_registry,
                application_layout=layout,
                runtime_services=runtime_services,
                agent_settings=AgentSettings.from_config(
                    config_resolution.config
                ),
                feature_settings=FeatureSettings.from_config(
                    config_resolution.config
                ),
            )
        except BaseException:
            report.close()
            raise

        try:
            await pref.load_pref()
            service_endpoints.configure(
                await ServiceConfig(config_session).load_domain()
            )
            await mind.external_mcp.start()
            turn_application = runtime_services.create_turn_application(
                agent_runtime_db_path()
            )
            return cls(
                mind,
                report=report,
                turn_runner=turn_runner,
                environment_snapshot_provider=environment_snapshot_provider,
                turn_application=turn_application,
            )
        except BaseException:
            try:
                await mind.close_runtime_resources()
            finally:
                report.close()
            raise

    async def close(self) -> None:
        """关闭应用运行时，并最后释放进程报告。"""
        try:
            await self.mind.end_conversation(reason="exit")
        finally:
            try:
                await self._turn_application.close(cancel_running=True)
            finally:
                try:
                    await self.mind.close_runtime_resources()
                finally:
                    self._report.close()

    async def execute(
        self,
        *,
        prompt: str,
        sandbox_mode: SandboxMode | None,
        approval_policy: ApprovalPolicy | None,
        working_directory: str | None,
        timeout_sec: float | None = DEFAULT_MCP_EXEC_TIMEOUT_SEC,
        session_id: str | None = None
    ) -> MindMcpExecutionResult:
        """串行执行一次支持超时和会话续接的模型请求。"""
        message = str(prompt or "").strip()
        if not message:
            return self._failed("prompt is empty")

        if timeout_sec is not None and (
            not math.isfinite(timeout_sec) or timeout_sec <= 0.0
        ):
            return self._failed("timeout_sec must be a positive finite number or null")

        try:
            workspace = (
                Path(working_directory).expanduser().resolve()
                if working_directory
                else self.default_workspace
            )
        except (OSError, RuntimeError, ValueError) as error:
            return self._failed(f"working directory is invalid: {error}")
        if not workspace.is_dir():
            return self._failed(f"working directory is unavailable: {workspace}")

        request = _McpRequestState()

        current_permissions = getattr(self.mind, "permissions", None)
        effective_sandbox_mode: SandboxMode = (
            getattr(current_permissions, "sandbox_mode", "read-only")
            if sandbox_mode is None
            else sandbox_mode
        )
        effective_approval_policy: ApprovalPolicy = (
            getattr(current_permissions, "approval_policy", "on-request")
            if approval_policy is None
            else approval_policy
        )
        effective_approval_reviewer: ApprovalReviewer = getattr(
            current_permissions,
            "approvals_reviewer",
            "user",
        )
        effective_network_access = normalize_network_access(
            getattr(
                current_permissions,
                "network_access",
                "restricted",
            )
        )
        permissions = PermissionSettings(
            sandbox_mode=effective_sandbox_mode,
            approval_policy=effective_approval_policy,
            approvals_reviewer=effective_approval_reviewer,
            network_access=effective_network_access,
        )

        try:
            async with asyncio.timeout(timeout_sec):
                async with self._call_lock:
                    return await self._execute_locked(
                        message=message,
                        permissions=permissions,
                        workspace=workspace,
                        requested_session_id=(session_id or "").strip() or None,
                        request=request,
                    )
        except TimeoutError:
            assert timeout_sec is not None
            return self._failed(
                f"request timed out after {timeout_sec:g} seconds",
                session_id=request.session_id,
            )

    async def _execute_locked(
        self,
        *,
        message: str,
        permissions: PermissionSettings,
        workspace: Path,
        requested_session_id: str | None,
        request: _McpRequestState
    ) -> MindMcpExecutionResult:
        """在持有调用锁时选择会话并执行请求。"""
        self.mind.set_history_workspace(workspace)

        if requested_session_id is None:
            metadata = await self.mind.reset_conversation(
                reason="mcp_tool_call",
                source="mcp_server",
            )
        else:
            record = self.mind.find_conversation_session(
                requested_session_id,
                workspace=workspace,
            )
            if record is None:
                return self._failed(
                    "session_id is unavailable for this working directory"
                )
            metadata = await self.mind.resume_conversation(
                record,
                source="mcp_server",
            )
            if metadata is None:
                return self._failed("session_id is invalid")

        request.session_id = metadata["sid"]

        environment_snapshot = self._environment_snapshot_provider(
            self.mind,
            cwd=workspace,
            workspace_root=workspace,
        )
        command = SubmitTurnCommand.create(
            session_id=request.session_id,
            message=message,
            environment_snapshot=environment_snapshot,
            extras={
                "working_directory": str(workspace),
                "sandbox_mode": permissions.sandbox_mode,
                "approval_policy": permissions.approval_policy,
                "approvals_reviewer": permissions.approvals_reviewer,
                "network_access": permissions.network_access,
            },
        )

        execute_root_turn = RootTurnCommandExecutor(
            functools.partial(self._turn_runner, self.mind),
            permissions=permissions,
        )

        execution = await self._turn_application.submit(
            command,
            execute_root_turn,
        )

        return MindMcpExecutionResult(
            run=execution.value,
            session_id=request.session_id,
            projection=execution.projection,
        )

    @staticmethod
    def _failed(
        error: str,
        *,
        session_id: str | None = None
    ) -> MindMcpExecutionResult:
        """构造 MCP 工具调用的结构化失败结果。"""
        return MindMcpExecutionResult(
            run=RunResult(status="failed", error=error),
            session_id=session_id,
        )


def create_mind_mcp_server(
    *,
    entry_file: str | None = None,
    layout: ApplicationLayout | None = None,
    config_overrides: tuple[ConfigOverride, ...] = (),
    config_profile: str | None = None,
    runtime_services: RuntimeServices,
    turn_runner: RootTurnRunner = _require_turn_runner,
    environment_snapshot_provider: EnvironmentSnapshotProvider = (
        _require_environment_snapshot
    ),
) -> FastMCP[MindMcpRuntime]:
    """创建提供 agent 工具的 stdio MCP 服务。"""
    resolved_layout = layout or resolve_application_layout(entry_file=entry_file)

    @contextlib.asynccontextmanager
    async def lifespan(
        _: FastMCP[MindMcpRuntime]
    ) -> typing.AsyncIterator[MindMcpRuntime]:
        runtime = await MindMcpRuntime.open(
            resolved_layout,
            config_overrides,
            config_profile,
            runtime_services=runtime_services,
            turn_runner=turn_runner,
            environment_snapshot_provider=environment_snapshot_provider,
        )
        try:
            yield runtime
        finally:
            await runtime.close()

    server: FastMCP[MindMcpRuntime] = FastMCP(
        name=const.APP_DESC,
        instructions=(
            f"Use mind_exec to run {const.APP_DESC} agent tasks in the configured "
            "workspace. Calls are serialized; pass a returned session_id to "
            "continue a session."
        ),
        website_url=const.APP_URL,
        log_level="WARNING",
        lifespan=lifespan,
    )

    @server.tool(
        name="mind_exec",
        title=f"{const.APP_DESC} Exec",
        description=(
            f"执行 {const.APP_DESC} agent 任务，并支持超时、取消和可选会话续接。"
        ),
        structured_output=True,
    )
    async def mind_exec(
        prompt: str,
        context: Context[typing.Any, MindMcpRuntime, typing.Any],
        sandbox_mode: SandboxMode | None = None,
        approval_policy: ApprovalPolicy | None = None,
        working_directory: str | None = None,
        timeout_sec: float | None = DEFAULT_MCP_EXEC_TIMEOUT_SEC,
        session_id: str | None = None
    ) -> dict[str, typing.Any]:
        """执行一次 agent 任务并返回结构化结果。"""
        runtime = context.request_context.lifespan_context

        result = await runtime.execute(
            prompt=prompt,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
            working_directory=working_directory,
            timeout_sec=timeout_sec,
            session_id=session_id,
        )
        return result.to_dict()

    return server


async def run_mind_mcp_server(
    *,
    entry_file: str | None = None,
    config_overrides: tuple[ConfigOverride, ...] = (),
    config_profile: str | None = None,
    runtime_services: RuntimeServices,
    turn_runner: RootTurnRunner = _require_turn_runner,
    environment_snapshot_provider: EnvironmentSnapshotProvider = (
        _require_environment_snapshot
    ),
) -> int:
    """通过 stdio 运行 MCP 服务直至客户端断开。"""
    server = create_mind_mcp_server(
        entry_file=entry_file,
        config_overrides=config_overrides,
        config_profile=config_profile,
        runtime_services=runtime_services,
        turn_runner=turn_runner,
        environment_snapshot_provider=environment_snapshot_provider,
    )
    await server.run_stdio_async()
    return 0


if __name__ == '__main__':
    pass
