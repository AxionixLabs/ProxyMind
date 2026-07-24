# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
import asyncio
import contextlib
from pathlib import Path
from mcp.server.fastmcp import (
    Context,
    FastMCP
)
from mind_app.controller import Mind
from mind_app.frontend.contracts import Frontend
from mind_app.frontend.sinks import NullApplicationSink
from mind_app.interaction import NonInteractiveInteraction
from mind_app.modes.result import RunResult
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
from mind_core.preference import Preferences
from mind_core.service_config import ServiceConfig
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    RunMode
)
from mind_nova.requests.access import (
    AccessMode,
    DEFAULT_ACCESS_MODE
)
from mind_nova.services import service_endpoints
from mind_nova import const


class MindMcpRuntime(object):
    """管理 stdio MCP 服务持有的长驻 Mind 运行时。"""

    def __init__(self, mind: Mind) -> None:
        """绑定 Mind 实例并初始化串行调用锁。"""
        self.mind              = mind
        self.default_workspace = Path(mind.history_workspace).resolve()

        self._call_lock = asyncio.Lock()

    @classmethod
    async def open(cls, layout: ApplicationLayout) -> "MindMcpRuntime":
        """创建并启动 MCP 服务使用的 Mind 运行时。"""
        home   = ensure_mind_home()
        report = RunReport(str(mind_reports_dir()), label="mcp_server")
        pref   = Preferences(str(mind_config_path()))

        route_shell_tools(layout.supports)
        clear_exec_env_cache()

        frontend = Frontend(
            application=NullApplicationSink(),
            interaction=NonInteractiveInteraction(),
            session_factory=create_silent_output_session,
        )

        mind = Mind(
            const.SHOW_LEVEL,
            os.cpu_count() or 1,
            {},
            src_opera_place=str(home),
            src_total_place=str(mind_reports_dir()),
            pref=pref,
            animate=False,
            frontend=frontend,
            design=None,
            report=report,
            workspace_root=Path.cwd(),
        )

        mind.bind_runtime(asyncio.get_running_loop(), asyncio.current_task())

        try:
            await pref.load_pref()
            service_endpoints.configure(await ServiceConfig().load_domain())
            await mind.start_external_mcp_runtime()
        except BaseException:
            await mind.close_runtime_resources()
            raise

        return cls(mind)

    async def close(self) -> None:
        """关闭 Mind 持有的外部 MCP、工具和报告资源。"""
        await self.mind.close_runtime_resources()

    async def execute(
        self,
        *,
        prompt: str,
        mode: RunMode,
        access_mode: AccessMode,
        working_directory: str | None,
    ) -> RunResult:
        """在隔离会话中串行执行一次 Mind 请求。"""
        message = str(prompt or "").strip()
        if not message:
            return RunResult(status="failed", error="prompt is empty")

        async with self._call_lock:
            try:
                workspace = (
                    Path(working_directory).expanduser().resolve()
                    if working_directory
                    else self.default_workspace
                )
            except (OSError, RuntimeError, ValueError) as error:
                return RunResult(
                    status="failed",
                    error=f"working directory is invalid: {error}",
                )
            if not workspace.is_dir():
                return RunResult(
                    status="failed",
                    error=f"working directory is unavailable: {workspace}",
                )
            self.mind.set_history_workspace(workspace)

            self.mind.reset_conversation(
                reason="mcp_tool_call",
                source="mcp_server",
            )
            return await self.mind.calling(
                message=message,
                mode=mode,
                access_mode=access_mode,
            )


def create_mind_mcp_server(
    *,
    entry_file: str | None = None,
    layout: ApplicationLayout | None = None,
) -> FastMCP[MindMcpRuntime]:
    """创建提供 Mind agent 工具的 stdio MCP 服务。"""
    resolved_layout = layout or resolve_application_layout(entry_file=entry_file)

    @contextlib.asynccontextmanager
    async def lifespan(
        _: FastMCP[MindMcpRuntime],
    ) -> typing.AsyncIterator[MindMcpRuntime]:
        runtime = await MindMcpRuntime.open(resolved_layout)
        try:
            yield runtime
        finally:
            await runtime.close()

    server: FastMCP[MindMcpRuntime] = FastMCP(
        name=const.APP_DESC,
        instructions=(
            "Use mind_exec to run one isolated Mind agent task in the configured "
            "workspace. Calls are serialized by the server."
        ),
        website_url=const.APP_URL,
        log_level="WARNING",
        lifespan=lifespan,
    )

    @server.tool(
        name="mind_exec",
        title="Mind Exec",
        description="执行一次隔离的 Mind agent 任务并返回结构化结果。",
        structured_output=True,
    )
    async def mind_exec(
        prompt: str,
        context: Context[typing.Any, MindMcpRuntime, typing.Any],
        mode: RunMode = DEFAULT_RUN_MODE,
        access_mode: AccessMode = DEFAULT_ACCESS_MODE,
        working_directory: str | None = None,
    ) -> dict[str, typing.Any]:
        """执行一次隔离的 Mind agent 任务并返回结构化结果。"""
        runtime = context.request_context.lifespan_context
        result = await runtime.execute(
            prompt=prompt,
            mode=mode,
            access_mode=access_mode,
            working_directory=working_directory,
        )
        return result.to_dict()

    return server


async def run_mind_mcp_server(
    *,
    entry_file: str | None = None,
) -> int:
    """通过 stdio 运行 Mind MCP 服务直至客户端断开。"""
    server = create_mind_mcp_server(entry_file=entry_file)
    await server.run_stdio_async()
    return 0


if __name__ == '__main__':
    pass
