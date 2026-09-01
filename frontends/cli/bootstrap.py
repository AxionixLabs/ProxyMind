# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
import asyncio
from agent.application import RuntimeServices
from pathlib import Path
from infrastructure.platform.animation import AsyncAnimManager
from infrastructure.services.server_manager import ServerManage
from infrastructure.errors import AppError
from infrastructure.config.schema import ConfigOverride
from infrastructure.config.layers import ConfigResolution
from agent.application.config.settings import (
    AgentSettings,
    FeatureSettings,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from agent.domain.policies import (
    PermissionSettings,
    resolve_permissions
)
from observability import (
    observe,
    observe_exception
)
from infrastructure.config.paths import (
    ApplicationLayout,
    resolve_application_layout
)
from infrastructure.config.preferences import Preferences
from infrastructure.services.service_config import ServiceConfig
from protocol.transport.endpoints import service_endpoints
from metadata import const
from mind_app.controller import Mind
from agent.ports.presentation import (
    ApplicationView,
    StyledBlock,
    TextSpan,
    TextStyle,
)
from mind_app.presentation.application import (
    Frontend,
)
from infrastructure.config.runtime_paths import (
    ensure_mind_home,
    mind_config_path,
    mind_reports_dir,
    process_env
)
from observability.reporting import RunReport
from infrastructure.platform.shell_tools import route_shell_tools
from infrastructure.platform.workspace_context import fetch_runtime_workspace_root
from infrastructure.services.runtime_context import (
    ServiceRuntimeContext,
    ServiceRuntimeSpec,
)
from infrastructure.services.runtime_setup import resolve_service_runtime
from mind_app.runtime.mcp.service_runtime import (
    ensure_service_runtime_asset,
    prepare_and_start_service_runtime,
)
from infrastructure.services.helix_capability import ServerManageHelixCapability
from mind_app.presentation.terminal.contracts import TerminalDesign
from agent.ports import (
    ExecutionPolicy,
    HookRegistryPort,
    ProtocolCommandClient,
    RetryStatePort,
    TurnAnimationPort,
    TurnSessionContextPort,
    TurnSessionStatePort,
)
from agent.domain.tool_policy import ToolFilterMode
from .commands import (
    ApplicationCommand,
    ExecCommand,
    InteractiveCommand,
    ResumeCommand,
    RuntimeCommand,
    RuntimeUpgradeCommand,
    command_helix_profile,
    command_uses_helix
)
from .dispatch import (
    EnvironmentSnapshotProvider,
    RootTurnRunner,
    run_selected_command,
)
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


class _DirectoryTrustRuntime(typing.Protocol):
    """描述启动阶段目录信任界面需要的运行时能力。"""

    async def begin_directory_trust(
        self,
        cwd: Path,
        trust_target: Path
    ) -> None: ...

    async def wait_directory_trust(self) -> bool: ...

    def show_directory_trust_error(self, message: str) -> None: ...


def _emit_startup_warnings(
    frontend: Frontend,
    warnings: typing.Iterable[str],
    *,
    process_output: bool = False
) -> None:
    """输出配置解析阶段产生的可恢复告警。"""
    items = tuple(warnings)
    if not items:
        return None

    plain_parts: list[str] = []
    spans: list[TextSpan]  = []

    for index, warning in enumerate(items):
        if index:
            plain_parts.append("\n")
            spans.append(TextSpan("\n"))
        plain_parts.append(f"Warning: {warning}")
        spans.extend((
            TextSpan(
                "Warning: ",
                TextStyle(foreground="#FFD75F", bold=True),
            ),
            TextSpan(warning),
        ))

    frontend.application.emit(ApplicationView(
        type="config.warning",
        renderable=StyledBlock(
            plain_text="".join(plain_parts),
            spans=tuple(spans),
        ),
        payload={
            "warnings": items,
            **({"stream": "stderr"} if process_output else {}),
        },
    ))


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


async def _confirm_tui_project_trust(
    *,
    runtime: _DirectoryTrustRuntime,
    config_session: ConfigSession,
    resolution: ConfigResolution,
    workspace: Path
) -> ConfigResolution | None:
    """确认未知项目并保留界面，直到主画布上下文准备完成。"""
    project_trust = resolution.project_trust
    if project_trust is None or project_trust.level is not None:
        return resolution

    await runtime.begin_directory_trust(workspace, project_trust.trust_root)

    while await runtime.wait_directory_trust():
        try:
            trusted_resolution = config_session.set_project_trust(
                project_trust,
                "trusted",
            )
        except (OSError, TypeError, ValueError) as error:
            runtime.show_directory_trust_error(
                f"Failed to set trust for {project_trust.trust_root}: {error}"
            )
            continue

        return trusted_resolution

    return None


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
    config_profile: str | None,
    runtime_services: RuntimeServices | None = None,
    *,
    turn_runner: RootTurnRunner | None = None,
    environment_snapshot_provider: EnvironmentSnapshotProvider | None = None,
) -> int:
    """执行普通应用运行时的完整生命周期。"""
    output_mode = resolve_cli_output_mode(command)
    frontend    = resolve_cli_frontend(output_mode)
    design      = resolve_cli_design(frontend, output_mode)
    tui_runtime = None

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
            application_root=str(app_layout.root),
        )
    if runtime_spec is None:
        raise AppError(f"This platform is not supported: {platform}.")

    home    = ensure_mind_home()
    reports = mind_reports_dir()

    try:
        workspace = Path.cwd()

        config_session = ConfigSession(
            ConfigStore(mind_config_path()),
            config_overrides,
            profile=config_profile,
            workspace=workspace,
        )

        config_resolution = config_session.resolve()

        if output_mode == "tui":
            from frontends.tui.core.keymap import TuiRuntimeKeymap
            from frontends.tui.core.runtime import require_tui_runtime

            tui_runtime = require_tui_runtime(frontend.runtime)

            tui_runtime.configure_keymap(
                TuiRuntimeKeymap.from_config(config_resolution.config)
            )

            tui_runtime.configure_scrollback_reflow_line_limit(
                config_resolution.config["tui"][
                    "scrollback_reflow_line_limit"
                ]
            )

            if isinstance(command, (InteractiveCommand, ResumeCommand)):
                tui_runtime.begin_startup_gate()

    except (OSError, TypeError, ValueError) as error:
        raise AppError(f"Configuration is invalid: {error}") from error

    report = RunReport(str(reports))
    power  = os.cpu_count() or 1

    is_upgrade = isinstance(command, RuntimeUpgradeCommand)

    observe(
        "app.start",
        version=const.APP_VERSION,
        platform=platform,
        output_mode=output_mode,
        cpu_count=power,
        helix_requested=(
            False
            if isinstance(command, RuntimeUpgradeCommand)
            else command_uses_helix(command)
        ),
        upgrade=is_upgrade,
    )

    try:
        preference = Preferences(config_session)

        service_context = ServiceRuntimeContext(
            spec=runtime_spec,
            platform=platform,
            packaged=packaged,
            env_symbol=os.path.pathsep,
            app_desc=const.APP_DESC,
        )

        route_shell_tools(supports)
        if runtime_services is not None:
            runtime_services.environment_capability.clear_cache()

        observe(
            "runtime.resolved",
            packaged=packaged,
            executable=runtime_spec.executable,
        )

    except BaseException as error:
        observe_exception("app.bootstrap.failed", error)
        report.close()
        raise

    if isinstance(command, RuntimeUpgradeCommand):
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

    if tui_runtime is not None:
        try:
            trusted_resolution = await _confirm_tui_project_trust(
                runtime=tui_runtime,
                config_session=config_session,
                resolution=config_resolution,
                workspace=workspace,
            )
        except BaseException:
            report.close()
            if tui_runtime.active:
                await tui_runtime.close()
            raise

        if trusted_resolution is None:
            observe("app.trust_declined")
            report.close()
            await tui_runtime.close()
            return 0

        config_resolution = trusted_resolution

    try:
        permissions = resolve_permissions(
            config_resolution.config,
            interactive=output_mode == "tui",
        )
    except BaseException as error:
        observe_exception("app.bootstrap.failed", error)
        report.close()
        if tui_runtime is not None:
            if tui_runtime.active:
                await tui_runtime.close()
        raise

    try:
        hook_registry = runtime_services.create_hook_registry(
            bypass_hook_trust=(
                command.bypass_hook_trust
                if isinstance(command, ExecCommand)
                else False
            )
        )

        agent_settings   = AgentSettings.from_config(config_resolution.config)
        feature_settings = FeatureSettings.from_config(config_resolution.config)

        hook_startup_warnings = (
            hook_registry.startup_warnings(
                config_resolution.hooks,
                hook_states=config_resolution.hook_states,
                warnings=config_resolution.hook_warnings,
            )
            if isinstance(command, ExecCommand)
            else ()
        )

    except BaseException as error:
        observe_exception("app.bootstrap.failed", error)
        report.close()
        if tui_runtime is not None:
            if tui_runtime.active:
                await tui_runtime.close()
        raise

    try:
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
            application_layout=app_layout,
            hook_registry=hook_registry,
            hook_startup_warnings=hook_startup_warnings,
            agent_settings=agent_settings,
            feature_settings=feature_settings,
            runtime_services=runtime_services,
            turn_runner=turn_runner,
            environment_snapshot_provider=environment_snapshot_provider,
            startup_warnings=(
                *config_resolution.startup_warnings,
                *(
                    config_resolution.project_trust_warnings
                    if isinstance(command, ExecCommand)
                    else ()
                ),
                *(
                    config_resolution.hook_warnings
                    if not isinstance(command, ExecCommand)
                    else ()
                ),
            ),
        )

    except BaseException:
        if tui_runtime is not None:
            if tui_runtime.active:
                await tui_runtime.close()
        raise


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
    application_layout: ApplicationLayout | None = None,
    hook_registry: HookRegistryPort | None = None,
    hook_startup_warnings: tuple[str, ...] = (),
    agent_settings: AgentSettings | None = None,
    feature_settings: FeatureSettings | None = None,
    startup_warnings: tuple[str, ...] = (),
    runtime_services: RuntimeServices | None = None,
    turn_runner: RootTurnRunner | None = None,
    environment_snapshot_provider: EnvironmentSnapshotProvider | None = None,
) -> int:
    """创建 Controller 并运行用户命令。"""
    hook_status = None

    try:
        if output_mode == "tui":
            from frontends.tui.adapters.hooks import TuiHookStatusAdapter
            from frontends.tui.core.runtime import require_tui_runtime

            hook_status = TuiHookStatusAdapter(
                require_tui_runtime(frontend.runtime)
            )

        server = ServerManage(
            runtime_spec.launch_command,
            env=process_env(),
            cwd=runtime_spec.working_directory,
        )

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
            hook_registry=hook_registry,
            hook_startup_warnings=hook_startup_warnings,
            hook_status=hook_status,
            application_layout=application_layout,
            runtime_services=runtime_services,
            agent_settings=agent_settings or AgentSettings(),
            feature_settings=feature_settings or FeatureSettings(),
        )

    except BaseException as error:
        observe_exception("app.initialize.failed", error)
        report.close()
        raise

    completed: bool = False

    interactive_tui = bool(
        output_mode == "tui"
        and isinstance(command, (InteractiveCommand, ResumeCommand))
    )
    config_service = None

    try:
        configured_helix = getattr(runtime_services, "helix_capability", None)
        helix_capability = (
            configured_helix
            if configured_helix is not None
            else ServerManageHelixCapability(server)
        )
        controller.service_runtime.bind(
            server,
            service_context,
            capability=helix_capability,
        )

        if output_mode == "tui":
            from frontends.tui.core.runtime import require_tui_runtime
            from frontends.tui.session.state import preload_tui_prompt_context

            if interactive_tui:
                require_tui_runtime(
                    controller.frontend.runtime
                ).begin_startup_gate()
            await preload_tui_prompt_context(controller)

        _emit_startup_warnings(
            frontend,
            startup_warnings,
            process_output=output_mode in {"text", "json"},
        )

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

        if output_mode == "tui":
            from server import ConfigServiceRuntime

            config_service = ConfigServiceRuntime(
                config_session,
                log_level=const.SHOW_LEVEL,
            )
            await config_service.start()
            observe("config_service.started")

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
            if output_mode == "tui" and not interactive_tui:
                await start_tui_external_mcp(controller)
            elif output_mode != "tui":
                await controller.external_mcp.start()

            await preference_task
            service_endpoints.configure(await domain_task)
            external_runtime = controller.external_mcp.current

            observe(
                "startup.ready",
                external_mcp=bool(
                    external_runtime is not None
                    and external_runtime.group is not None
                ),
                helix_linked=controller.is_service_mcp_linked(),
            )

        finally:
            for task in startup_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*startup_tasks, return_exceptions=True)

        if output_mode == "tui":
            from frontends.tui.core.runtime import require_tui_runtime
            from frontends.tui.features.helix import confirm_tui_service_runtime_startup
            from frontends.tui.features.hooks import (
                manage_hooks,
                review_startup_hooks
            )

            runtime = require_tui_runtime(controller.frontend.runtime)

            start_helix: bool = False

            try:
                if interactive_tui:
                    startup_hooks = await review_startup_hooks(
                        runtime,
                        controller,
                    )
                    if startup_hooks is not None:
                        await runtime.settle_startup_gate()
                        await manage_hooks(
                            runtime,
                            controller,
                            catalog=startup_hooks,
                        )
                        runtime.start_background_task(
                            start_tui_external_mcp(controller),
                            name="tui external mcp startup",
                        )

                    if runtime.startup_gate_active:
                        await runtime.finish_startup_gate()
                    if startup_hooks is None:
                        runtime.start_background_task(
                            start_tui_external_mcp(controller),
                            name="tui external mcp startup",
                        )
                    await asyncio.sleep(0)

                if helix_profile is not None:
                    start_helix = await confirm_tui_service_runtime_startup(controller)
                    if not start_helix:
                        _emit_helix_skipped(controller)
            finally:
                if runtime.startup_gate_active:
                    await runtime.finish_startup_gate()

            if start_helix and helix_profile is not None:
                runtime.start_background_task(
                    start_tui_service_runtime(
                        controller,
                        tool_profile=helix_profile,
                    ),
                    name="tui service runtime startup",
                )

        model_capability = None
        protocol_client = None
        turn_application_factory = None
        effect_journal_factory = None
        approval_ledger = None
        session_factory = None
        transcript_factory = None
        cleanup = None
        patch_preview = None
        retry_state: RetryStatePort | None = None
        animation_port: TurnAnimationPort | None = None
        session_context: TurnSessionContextPort | None = None
        session_state: TurnSessionStatePort | None = None
        execution_policy: ExecutionPolicy | None = None
        if runtime_services is not None:
            turn_application_factory = runtime_services.create_turn_application
            model_capability = runtime_services.model_capability
            effect_journal_factory = runtime_services.create_effect_journal
            approval_ledger = controller.approval_call_ledger
            session_factory = controller.frontend.session_factory
            transcript_factory = controller.transcripts.writer
            cleanup = controller
            patch_preview = controller.workspace_runtime.coding.preview_patch
            retry_state = controller.frontend.runtime
            animation_port = controller.turn_animation
            session_context = controller.turn_session_context
            session_state = controller.turn_session_state
            execution_policy = controller.workspace_runtime.execution_policy
            if isinstance(
                runtime_services.model_capability,
                ProtocolCommandClient,
            ):
                protocol_client = runtime_services.model_capability

        await run_selected_command(
            controller,
            command,
            turn_runner=turn_runner,
            environment_snapshot_provider=environment_snapshot_provider,
            turn_application_factory=turn_application_factory,
            model_capability=model_capability,
            protocol_client=protocol_client,
            effect_journal_factory=effect_journal_factory,
            approval_ledger=approval_ledger,
            session_factory=session_factory,
            transcript_factory=transcript_factory,
            cleanup=cleanup,
            patch_preview=patch_preview,
            retry_state=retry_state,
            animation=animation_port,
            session_context=session_context,
            session_state=session_state,
            execution_policy=execution_policy,
        )
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
        try:
            await finalize_application(
                controller,
                output_mode=output_mode,
                completed=completed,
            )
        finally:
            try:
                if config_service is not None:
                    await config_service.stop()
                    observe("config_service.stopped")
            finally:
                report.close()


async def start_tui_external_mcp(controller: Mind) -> None:
    """启动 TUI 外部 MCP 并提交最终状态。"""
    from frontends.tui.features.mcp import (
        finish_mcp_activity,
        render_external_mcp_start_status
    )

    try:
        await controller.external_mcp.start(defer_activity_stop=True)
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
    tool_profile: ToolFilterMode = "app"
) -> None:
    """在 TUI 后台准备 Helix 服务运行时。"""
    from frontends.tui.features.helix import (
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
    config_profile: str | None = None,
    runtime_services: RuntimeServices,
    turn_runner: RootTurnRunner | None = None,
    environment_snapshot_provider: EnvironmentSnapshotProvider | None = None,
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
            runtime_services,
            turn_runner=turn_runner,
            environment_snapshot_provider=environment_snapshot_provider,
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
                approval_coordinator = getattr(
                    controller,
                    "approval_coordinator",
                    None,
                )
                if approval_coordinator is not None:
                    await approval_coordinator.close()
                await controller.frontend.runtime.close()
            except BaseException as error:
                observe_exception("frontend.close.failed", error)
                raise
    finally:
        await controller.close_runtime_resources()

    if completed and output_mode == "tui":
        from frontends.tui.core.runtime import require_tui_runtime

        runtime = require_tui_runtime(controller.frontend.runtime)

        conversation = controller.conversation
        if conversation.turn_count > 0 and conversation.sid:
            runtime.print_exit_summary(conversation.sid)


if __name__ == '__main__':
    pass
