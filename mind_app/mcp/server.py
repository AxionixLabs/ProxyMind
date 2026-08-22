# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import math
import typing
import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
from mcp.server.fastmcp import (
    Context,
    FastMCP
)
from mind_app.controller import Mind
from mind_app.frontend.contracts import Frontend
from mind_app.frontend.sinks import NullApplicationSink
from mind_app.interaction import NonInteractiveInteraction
from mind_app.runtime.turns.result import RunResult
from mind_app.output.silent import create_silent_output_session
from mind_app.paths import (
    ensure_mind_home,
    mind_config_path,
    mind_reports_dir
)
from mind_app.reporting import RunReport
from mind_app.runtime.environment.exec_env import clear_exec_env_cache
from mind_app.runtime.environment.shell_tools import route_shell_tools
from mind_core.application_paths import (
    ApplicationLayout,
    resolve_application_layout
)
from mind_core.agent_config import AgentSettings
from mind_core.config import ConfigOverride
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.feature_config import FeatureSettings
from mind_core.preference import Preferences
from mind_core.permissions import (
    PermissionSettings,
    resolve_permissions
)
from mind_app.runtime.hooks.registry import HookRegistry
from mind_core.service_config import ServiceConfig
from mind_nova.requests.permissions import (
    ApprovalPolicy,
    ApprovalReviewer,
    SandboxMode
)
from mind_nova.services import service_endpoints
from mind_nova import const

DEFAULT_MCP_EXEC_TIMEOUT_SEC = 900.0


@dataclass(frozen=True, slots=True)
class MindMcpExecutionResult(object):
    """描述一次 MCP 工具调用及其可续接会话。"""
    run: RunResult
    session_id: str | None = None

    def to_dict(self) -> dict[str, typing.Any]:
        """返回 MCP structured content 使用的字典。"""
        result = self.run.to_dict()
        result["session_id"] = self.session_id
        return result


class _McpRequestState(object):
    """记录单次 MCP 请求已经绑定的会话。"""

    def __init__(self) -> None:
        self.session_id: str | None = None


class MindMcpRuntime(object):
    """管理 stdio MCP 服务持有的长驻应用运行时。"""

    def __init__(self, mind: Mind) -> None:
        """绑定主控制器并初始化串行调用锁。"""
        self.mind              = mind
        self.default_workspace = Path(mind.history_workspace).resolve()

        self._call_lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        layout: ApplicationLayout,
        config_overrides: tuple[ConfigOverride, ...] = (),
        config_profile: str | None = None
    ) -> "MindMcpRuntime":
        """创建并启动 MCP 服务使用的应用运行时。"""
        home   = ensure_mind_home()
        report = RunReport(str(mind_reports_dir()), label="mcp_server")

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
        clear_exec_env_cache()

        frontend = Frontend(
            application=NullApplicationSink(),
            interaction=NonInteractiveInteraction(),
            session_factory=create_silent_output_session,
        )

        hook_registry = HookRegistry()

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
            agent_settings=AgentSettings.from_config(config_resolution.config),
            feature_settings=FeatureSettings.from_config(
                config_resolution.config
            ),
        )

        try:
            await pref.load_pref()
            service_endpoints.configure(
                await ServiceConfig(config_session).load_domain()
            )
            await mind.start_external_mcp_runtime()
        except BaseException:
            await mind.close_runtime_resources()
            raise

        return cls(mind)

    async def close(self) -> None:
        """关闭主控制器持有的外部 MCP、工具和报告资源。"""
        await self.mind.end_conversation(reason="exit")
        await self.mind.close_runtime_resources()

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
        permissions = PermissionSettings(
            sandbox_mode=effective_sandbox_mode,
            approval_policy=effective_approval_policy,
            approvals_reviewer=effective_approval_reviewer,
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

        run = await self.mind.calling(
            message=message,
            permissions=permissions,
        )

        return MindMcpExecutionResult(run=run, session_id=request.session_id)

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
    config_profile: str | None = None
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
    config_profile: str | None = None
) -> int:
    """通过 stdio 运行 MCP 服务直至客户端断开。"""
    server = create_mind_mcp_server(
        entry_file=entry_file,
        config_overrides=config_overrides,
        config_profile=config_profile,
    )
    await server.run_stdio_async()
    return 0


if __name__ == '__main__':
    pass
