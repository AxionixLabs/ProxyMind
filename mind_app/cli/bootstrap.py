# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
import asyncio
from pathlib import Path
from engine.animation import AsyncAnimManager
from engine.manage import ServerManage
from engine.errors import AppError
from mind_core.config import ConfigOverride
from mind_core.agent_config import AgentSettings
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.permissions import (
    PermissionSettings,
    resolve_permissions
)
from engine.observability import (
    observe,
    observe_exception
)
from mind_core.application_paths import resolve_application_layout
from mind_core.preference import Preferences
from mind_core.service_config import ServiceConfig
from mind_nova import const
from mind_nova.services import service_endpoints
from ..controller import Mind
from ..frontend.contracts import (
    ApplicationView,
    Frontend
)
from ..paths import (
    ensure_mind_home,
    mind_config_path,
    mind_reports_dir,
    process_env
)
from ..presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from ..reporting import RunReport
from ..runtime.environment.exec_env import clear_exec_env_cache
from ..runtime.environment.shell_tools import route_shell_tools
from ..runtime.environment.workspace import fetch_runtime_workspace_root
from ..runtime.mcp.service_runtime import (
    ServiceRuntimeContext,
    ServiceRuntimeSpec,
    ensure_service_runtime_asset,
    prepare_and_start_service_runtime,
    resolve_service_runtime
)
from ..runtime.design import TerminalDesign
from ..runtime.hooks.registry import HookRegistry
from ..runtime.tools.mode_policy import ToolFilterMode
from .commands import (
    AgentListenCommand,
    ApplicationCommand,
    HelixUpgradeCommand,
    RuntimeCommand,
    command_helix_profile,
    command_uses_helix
)
from .dispatch import run_selected_command
from .frontend import (
    resolve_cli_design,
    resolve_cli_frontend
)
from .selection import (
    OutputMode,
    output_mode_uses_animation,
    resolve_cli_output_mode
)

CleanupResult = typing.TypeVar("CleanupResult")


def _emit_helix_skipped(controller: Mind) -> None:
    """输出 Helix 启动被跳过的状态。"""
    controller.frontend.application.emit(ApplicationView(
        type="helix.skipped",
        renderable=StyledBlock(
            plain_text="Helix · skipped",
            spans=(
                TextSpan(
                    "Helix ",
                    TextStyle(foreground="#AFC7D8", bold=True),
                ),
                TextSpan(
                    "· skipped",
                    TextStyle(foreground="#7F8C9A", dim=True),
                ),
            ),
        ),
    ))
    controller.frontend.application.emit(ApplicationView(type="spacer"))


async def _await_cleanup(
    awaitable: typing.Awaitable[CleanupResult],
) -> CleanupResult:
    """在取消态下等待清理任务执行完成。"""
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _run_application(
    command: ApplicationCommand,
    entry_file: str | None,
    animation: AsyncAnimManager,
    config_overrides: tuple[ConfigOverride, ...],
    config_profile: str | None
) -> int:
    """执行普通应用运行时的完整生命周期。"""
    output_mode = resolve_cli_output_mode(command)
    frontend    = resolve_cli_frontend(output_mode)
    design      = resolve_cli_design(frontend, output_mode)

    if output_mode_uses_animation(output_mode):
        frontend.application.emit(ApplicationView(type="intro"))

    try:
        app_layout = resolve_application_layout(entry_file=entry_file)
    except ValueError as error:
        raise AppError(f"Application entry is unsupported: {error}") from error

    platform = app_layout.platform
    supports = str(app_layout.supports)
    packaged = app_layout.packaged

    runtime_spec = None

    if platform in {"win32", "darwin"}:
        runtime_spec = resolve_service_runtime(
            platform=platform,
            supports=supports,
            level=const.SHOW_LEVEL,
            packaged=packaged,
        )
    if runtime_spec is None:
        raise AppError(f"This platform is not supported: {platform}.")

    home    = ensure_mind_home()
    reports = mind_reports_dir()

    try:
        config_session = ConfigSession(
            ConfigStore(mind_config_path()),
            config_overrides,
            profile=config_profile,
            workspace=Path.cwd(),
        )

        config_resolution = config_session.resolve()

        if output_mode == "tui":
            from ..tui.core.keymap import TuiRuntimeKeymap
            from ..tui.core.runtime import require_tui_runtime

            tui_runtime = require_tui_runtime(frontend.runtime)

            tui_runtime.configure_keymap(
                TuiRuntimeKeymap.from_config(config_resolution.config)
            )

            tui_runtime.configure_scrollback_reflow_line_limit(
                config_resolution.config["tui"][
                    "scrollback_reflow_line_limit"
                ]
            )
    except (OSError, TypeError, ValueError) as error:
        raise AppError(f"Configuration is invalid: {error}") from error

    report = RunReport(str(reports))
    power  = os.cpu_count() or 1

    is_upgrade = isinstance(command, HelixUpgradeCommand)

    observe(
        "app.start",
        version=const.APP_VERSION,
        platform=platform,
        output_mode=output_mode,
        cpu_count=power,
        helix_requested=(
            False
            if isinstance(command, HelixUpgradeCommand)
            else command_uses_helix(command)
        ),
        upgrade=is_upgrade,
    )

    try:
        preference = Preferences(config_session)

        permissions = resolve_permissions(
            config_resolution.config,
            interactive=output_mode == "tui",
        )

        service_context = ServiceRuntimeContext(
            spec=runtime_spec,
            platform=platform,
            packaged=packaged,
            env_symbol=os.path.pathsep,
            app_desc=const.APP_DESC,
        )

        route_shell_tools(supports)
        clear_exec_env_cache()

        observe(
            "runtime.resolved",
            packaged=packaged,
            executable=runtime_spec.executable,
        )

    except BaseException as error:
        observe_exception("app.bootstrap.failed", error)
        report.close()
        raise

    if isinstance(command, HelixUpgradeCommand):
        try:
            await ensure_service_runtime_asset(
                service_context,
                explicit_upgrade=True,
                anim_manager=animation,
                design=design,
            )
            observe("upgrade.complete")
            return 0
        except BaseException as error:
            observe_exception("upgrade.failed", error)
            raise
        finally:
            report.close()

    hook_registry = HookRegistry()

    return await _run_controller(
        command,
        frontend=frontend,
        design=design,
        animation=animation,
        home=home,
        reports=reports,
        preference=preference,
        config_session=config_session,
        report=report,
        runtime_spec=runtime_spec,
        service_context=service_context,
        power=power,
        output_mode=output_mode,
        permissions=permissions,
        hook_registry=hook_registry,
        agent_settings=AgentSettings.from_config(config_resolution.config),
    )


async def _run_controller(
    command: RuntimeCommand,
    *,
    frontend: Frontend,
    design: TerminalDesign | None,
    animation: AsyncAnimManager,
    home: Path,
    reports: Path,
    preference: Preferences,
    config_session: ConfigSession,
    report: RunReport,
    runtime_spec: ServiceRuntimeSpec,
    service_context: ServiceRuntimeContext,
    power: int,
    output_mode: OutputMode,
    permissions: PermissionSettings,
    hook_registry: HookRegistry | None = None,
    agent_settings: AgentSettings | None = None
) -> int:
    """创建 Controller 并运行用户命令。"""
    try:
        server = ServerManage(runtime_spec.launch_command, env=process_env())

        controller = Mind(
            const.SHOW_LEVEL,
            power,
            {},
            src_opera_place=str(home),
            src_total_place=str(reports),
            pref=preference,
            config_session=config_session,
            anim_manager=animation,
            animate=output_mode_uses_animation(output_mode),
            frontend=frontend,
            design=design,
            report=report,
            permissions=permissions,
            hook_registry=hook_registry or HookRegistry(),
            agent_settings=agent_settings or AgentSettings(),
        )

    except BaseException as error:
        observe_exception("app.initialize.failed", error)
        report.close()
        raise

    controller.bind_server_manager(server)
    controller.bind_service_runtime_context(service_context)

    completed: bool = False

    try:
        if output_mode == "tui":
            from ..tui.session.state import preload_tui_prompt_context

            await preload_tui_prompt_context(controller)
        await controller.frontend.runtime.open()
        observe("frontend.opened", output_mode=output_mode)

        helix_profile = command_helix_profile(command)

        if helix_profile is not None and output_mode != "tui":
            helix_linked = await prepare_and_start_service_runtime(
                controller,
                tool_profile=helix_profile,
            )
            if not helix_linked:
                _emit_helix_skipped(controller)

        if output_mode == "tui" or isinstance(command, AgentListenCommand):
            await controller.start_config_service()

        runtime_workspace_root = await fetch_runtime_workspace_root()
        if runtime_workspace_root is not None:
            controller.set_history_workspace(runtime_workspace_root)

        preference_task = asyncio.create_task(
            preference.load_pref(),
            name="startup preference",
        )
        domain_task = asyncio.create_task(
            ServiceConfig(config_session).load_domain(),
            name="startup service domain",
        )
        startup_tasks = (preference_task, domain_task)

        try:
            if output_mode == "tui":
                await start_tui_external_mcp(controller)
            else:
                await controller.start_external_mcp_runtime()
            await preference_task
            service_endpoints.configure(await domain_task)
            observe(
                "startup.ready",
                external_mcp=bool(
                    controller.external_mcp is not None
                    and controller.external_mcp.group is not None
                ),
                helix_linked=controller.is_service_mcp_linked(),
            )
        finally:
            for task in startup_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*startup_tasks, return_exceptions=True)

        if output_mode == "tui":
            from ..tui.core.runtime import require_tui_runtime
            from ..tui.features.helix import confirm_tui_service_runtime_startup

            runtime = require_tui_runtime(controller.frontend.runtime)

            start_helix: bool = False

            if helix_profile is not None:
                start_helix = await confirm_tui_service_runtime_startup(controller)
                if not start_helix:
                    _emit_helix_skipped(controller)

            if start_helix and helix_profile is not None:
                runtime.start_background_task(
                    start_tui_service_runtime(
                        controller,
                        tool_profile=helix_profile,
                    ),
                    name="tui service runtime startup",
                )

        await run_selected_command(controller, command)
        completed = True
        observe("app.complete", exit_code=controller.exit_code)

        return controller.exit_code

    except asyncio.CancelledError:
        observe("app.interrupted", level="WARNING", output_mode=output_mode)
        raise

    except BaseException as error:
        observe_exception("app.failed", error, output_mode=output_mode)
        raise

    finally:
        await finalize_application(
            controller,
            output_mode=output_mode,
            completed=completed,
        )


async def start_tui_external_mcp(controller: Mind) -> None:
    """在 TUI 进入交互循环前启动外部 MCP。"""
    from ..tui.features.mcp import (
        finish_mcp_activity,
        render_external_mcp_start_status
    )

    try:
        await controller.start_external_mcp_runtime(defer_activity_stop=True)
    except asyncio.CancelledError:
        await controller.await_cleanup(finish_mcp_activity(controller, "start"))
        raise
    except Exception as error:
        await controller.await_cleanup(finish_mcp_activity(controller, "start"))
        render_external_mcp_start_status(controller, error=error)
        return None

    await finish_mcp_activity(controller, "start")
    render_external_mcp_start_status(controller)


async def start_tui_service_runtime(
    controller: Mind,
    *,
    tool_profile: ToolFilterMode = "app",
) -> None:
    """在 TUI 后台准备 Helix 服务运行时。"""
    from ..tui.features.helix import (
        finish_helix_activity,
        link_helix_runtime,
        render_helix_link_failure,
        render_helix_link_result
    )

    try:
        linked = await link_helix_runtime(
            controller,
            tool_profile,
            download_confirmed=True,
        )
    except asyncio.CancelledError:
        await controller.await_cleanup(finish_helix_activity(controller))
        raise
    except Exception as error:
        await controller.await_cleanup(finish_helix_activity(controller))
        render_helix_link_failure(controller, error)
        return None

    await finish_helix_activity(controller)
    render_helix_link_result(controller, linked)


async def run_application(
    command: ApplicationCommand,
    *,
    entry_file: str | None,
    config_overrides: tuple[ConfigOverride, ...] = (),
    config_profile: str | None = None
) -> int:
    """装配并运行需要本地应用资源的命令。"""
    animation = AsyncAnimManager()
    try:
        return await _run_application(
            command,
            entry_file,
            animation,
            config_overrides,
            config_profile,
        )
    finally:
        await _await_cleanup(animation.stop())


async def finalize_application(
    controller: Mind,
    *,
    output_mode: OutputMode,
    completed: bool
) -> None:
    """关闭前端和运行时资源，并在已有对话时打印恢复提示。"""
    observe(
        "app.shutdown.start",
        output_mode=output_mode,
        completed=completed,
        exit_code=controller.exit_code,
    )
    try:
        try:
            await controller.await_cleanup(controller.end_conversation(
                reason="exit" if completed else "error",
            ))
        except BaseException as error:
            observe_exception("session.close.failed", error)
            raise
        finally:
            try:
                await controller.frontend.runtime.close()
            except BaseException as error:
                observe_exception("frontend.close.failed", error)
                raise
    finally:
        await controller.close_runtime_resources()

    if completed and output_mode == "tui":
        from ..tui.core.runtime import require_tui_runtime

        runtime = require_tui_runtime(controller.frontend.runtime)
        conversation = controller.conversation
        if conversation.turn_count > 0 and conversation.sid:
            runtime.print_exit_summary(conversation.sid)


if __name__ == '__main__':
    pass
