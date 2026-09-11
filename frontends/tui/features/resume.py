# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from pathlib import Path

from agent.ports.workspace import WorkspaceChangePort
from agent.protocol.json_value import ThawedJsonValue
from agent.stores.sessions.history import (
    normalize_workspace,
    workspace_identity,
)
from frontends.terminal.text import sanitize_terminal_line
from frontends.tui.application import ResumeApplicationHost
from frontends.tui.contracts.menu import (
    MenuOption,
    MenuRequest,
)
from frontends.tui.core.runtime import (
    TuiRuntime,
    require_tui_runtime,
)
from frontends.tui.core.styles import failure_text_block
from frontends.tui.features.history import load_history_transcript
from frontends.tui.session.state import preload_tui_prompt_context
from infrastructure.config.layers import ConfigResolution
from infrastructure.config.session import ConfigSession
from infrastructure.skills import configured_skills
from protocol.schema.identifiers import valid_session_ids


ResumeCwdMode = typing.Literal["session", "current"]


async def choose_resume_directory(
    runtime: TuiRuntime,
    config: ConfigSession,
    *,
    current: Path,
    history_directory: str,
) -> tuple[Path | None, tuple[str, ...]]:
    """根据显式目录、记忆策略和本次选择决定恢复目录。"""
    if config.directory_override:
        return config.launch_directory, ()
    configured = config.load()["tui"].get("resume_cwd")
    if configured == "current":
        return config.launch_directory, ()
    if not history_directory.strip():
        if configured == "session":
            raise ValueError("Could not determine the working directory recorded for this session.")
        return current, ()
    session_directory = Path(normalize_workspace(history_directory))
    if configured == "session" or workspace_identity(current) == workspace_identity(session_directory):
        return session_directory, ()

    def interrupt() -> bool:
        """退出目录选择并由恢复调用方结束交互会话。"""
        runtime.finish_menu(None)
        return True

    selection = await runtime.select_menu(MenuRequest(
        title="Choose working directory to resume this session",
        view_id="resume:directory",
        body=(
            "Session = latest cwd recorded in the resumed session",
            "Current = your current working directory",
        ),
        options=(
            MenuOption("session", f"Use session directory ({sanitize_terminal_line(str(session_directory))})"),
            MenuOption("current", f"Use current directory ({sanitize_terminal_line(str(current))})"),
            MenuOption("remember_session", "Always use session directory"),
            MenuOption("remember_current", "Always use current directory"),
        ),
        selected=0,
        cancel_value="session",
        interrupt_on_eof=True,
        on_ctrl_c=interrupt,
        footer_hint="Press enter to continue",
        show_all_options=True,
    ))
    if selection is None:
        return None, ()
    warnings: tuple[str, ...] = ()
    remembered: ResumeCwdMode | None = None
    if selection == "remember_current":
        target = config.launch_directory
        remembered = "current"
    elif selection == "remember_session":
        target = session_directory
        remembered = "session"
    elif selection == "current":
        target = current
    elif selection == "session":
        target = session_directory
    else:
        raise ValueError("Invalid working directory selection.")
    if remembered is not None:
        try:
            config.update_user({("tui", "resume_cwd"): remembered})
        except (OSError, TypeError, ValueError) as error:
            warnings = (f"Could not save working directory preference: {error}",)
    return target, warnings


async def _resolve_target_config(
    runtime: TuiRuntime, config: ConfigSession, target: Path,
) -> ConfigResolution | None:
    """在活动工作区不变时解析目标配置并完成目标项目信任。"""
    resolution = config.resolve(workspace=target)
    trust = resolution.project_trust
    if trust is None or trust.level is not None:
        return resolution
    await runtime.begin_directory_trust(target, trust.trust_root)
    try:
        while await runtime.wait_directory_trust():
            try:
                return config.set_project_trust(trust, "trusted", workspace=target)
            except (OSError, TypeError, ValueError) as error:
                runtime.show_directory_trust_error(f"Failed to set trust for {trust.trust_root}: {error}")
        return None
    finally:
        await runtime.finish_directory_trust()


async def resume_history_session(
    host: ResumeApplicationHost,
    record: dict[str, ThawedJsonValue],
) -> bool:
    """执行 CLI 和应用内共享的恢复交互，成功后才替换历史画面。"""
    runtime = require_tui_runtime(host.frontend.runtime)
    cid = record.get("cid")
    sid = record.get("sid")
    if not isinstance(cid, str) or not isinstance(sid, str):
        raise ValueError("Session could not be resumed: invalid session cursor.")
    if not valid_session_ids(cid, sid):
        raise ValueError("Session could not be resumed: invalid session cursor.")
    if host.conversation.cid == cid and host.conversation.sid == sid:
        return False
    recorded_workspace = record.get("workspace")
    target, warnings = await choose_resume_directory(
        runtime, host.settings.config,
        current=Path(host.history_workspace),
        history_directory=recorded_workspace if isinstance(recorded_workspace, str) else "",
    )
    if target is None:
        host.lifecycle.request_stop()
        return False
    target = target.expanduser().resolve()
    if not target.is_dir():
        raise ValueError(f"Working directory is unavailable: {target}")

    replay_blocks = await asyncio.to_thread(
        load_history_transcript, host, sid,
        terminal_width=runtime.terminal_width,
        hyperlinks=runtime.hyperlinks_enabled,
        terminal_capabilities=runtime.terminal_capabilities,
        record=record,
    )
    change: WorkspaceChangePort | None = None
    if workspace_identity(target) != workspace_identity(host.history_workspace):
        resolution = await _resolve_target_config(runtime, host.settings.config, target)
        if resolution is None:
            host.lifecycle.request_stop()
            return False
        configured_skills(resolution.config, target)
        change = await host.prepare_workspace(target, resolution)
    try:
        if change is None:
            resumed = await host.conversation.resume(record, source="tui:resume")
        else:
            resumed = await host.conversation.resume(record, source="tui:resume", workspace_change=change)
        if resumed is None:
            raise ValueError("Session could not be resumed.")
    finally:
        if change is not None:
            warnings += await host.lifecycle.await_cleanup(change.finish())

    runtime.replace_transcript(replay_blocks)
    try:
        await preload_tui_prompt_context(host)
    except (OSError, TypeError, ValueError) as error:
        warnings += (f"Could not refresh prompt context: {error}",)
    for warning in warnings:
        runtime.append_block(failure_text_block(warning))
    return True


if __name__ == '__main__':
    pass
