"""以真实 TUI、Harness、SQLite、文件和 HTTP SDK 驱动隔离删除故障验收。"""

import argparse
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from agent.adapters.protocol.session_deletion import ProtocolSessionDeletionAdapter
from agent.application.agents.views import AgentSnapshot
from agent.application.config.session_identity import derive_local_session_id
from agent.application.hooks.context import HookExecutionContext
from agent.composition import open_turn_application
from agent.domain.policies import preset_permissions
from agent.harness.hooks.scope import HookExecutionScope
from agent.harness.hooks.session_lifecycle import SessionLifecycleGateway
from agent.harness.process_lifecycle import ProcessLifecycle
from agent.harness.sessions.root import RootConversationSession
from frontends.interaction.contracts import PromptContext
from frontends.terminal.capabilities import detect_terminal_capabilities
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.adapters.input import create_tui_input
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import text_block
from frontends.tui.session.barriers import TuiForegroundTasks
from frontends.tui.session.dispatch import (
    DispatchAction,
    TuiCommandDispatcher,
)
from infrastructure.persistence.conversation_history import LocalConversationHistory
from infrastructure.persistence.transcripts import ConversationTranscriptStore
from observability import reset_sinks
from protocol.transport.endpoints import service_endpoints
from tests.agent.stores.sessions.deletion_fixture import store
from tests.fakes.mind_chat import FakeMindChatServer
from tests.manual.session_delete_storage import load_fixture
from tests.pty.tui_scenario import ScenarioFacts


class EmptyRecovery:
    """隔离用例没有远端用量与压缩事件，不产生额外 HTTP 请求。"""

    async def load(self, *_args, **_kwargs):
        """返回未配置远端恢复事件的状态。"""
        return None


class EmptyHooks:
    """隔离目录没有配置 Hook，仍通过生产生命周期网关执行收束。"""

    def hook_scope(self, context: HookExecutionContext) -> HookExecutionScope:
        """按正式 Hook 契约返回空范围。"""
        return HookExecutionScope.empty(context)


async def run(directory: Path, url: str, mode: str) -> None:
    """在原生终端中驱动命令分派，写入真实持久化和脱敏验收事实。"""
    reset_sinks()
    plan, _ = load_fixture(directory)
    backend = store(directory, plan.root)
    transcripts = ConversationTranscriptStore(directory / "sessions")
    lifecycle = ProcessLifecycle()
    facts = ScenarioFacts(directory / f"{mode}.json", mode)
    history = LocalConversationHistory(
        backend.history,
        existing_transcript_path_for=transcripts.existing_path_for_session,
        transcript_entries_for=lambda path: transcripts.reader(path).read() if path else (),
    )
    service_endpoints.configure(url)

    async def cleanup(*_args) -> None:
        """本场景没有正在运行的 Hook、JavaScript 或事件连接。"""

    async def preferences(_ttl):
        """返回隔离场景的固定模型标签。"""
        return {"primary": {"model": "isolated-test"}}

    async def shutdown(sid: str) -> tuple[AgentSnapshot, ...]:
        """读取已完成的真实子代理图；不存在活动子任务可供终止。"""
        graph = backend.graphs.load(sid)
        return tuple(AgentSnapshot(record.thread, record.status) for record in graph.records) if graph else ()

    hooks = EmptyHooks()
    session = RootConversationSession(
        history, context_usage_recovery=EmptyRecovery(), compaction_recovery=EmptyRecovery(),
        workspace=lambda: str(directory), permissions=lambda: preset_permissions("auto"),
        preference_config=lambda: {"primary": {"model": "isolated-test"}}, fresh_preferences=preferences,
        permission_grants=None, approval_ledger=None, output_record_path="",
        transcript_factory=transcripts.writer, transcript_path_for=transcripts.path_for_session,
        hook_scope_provider=hooks,
        session_lifecycle=SessionLifecycleGateway(scope_factory=hooks.hook_scope, cleanup_session=cleanup),
        subagent_shutdown=shutdown, hook_session_cleanup=cleanup, javascript_session_cleanup=cleanup,
        command_hook_cleanup=lambda _sid: None, event_session_close=cleanup,
        await_cleanup=lifecycle.await_cleanup,
        session_deletion_store=backend, session_deletion_remote=ProtocolSessionDeletionAdapter(),
    )
    if mode == "initial":
        await session.bind(plan.root.cid, plan.root.sid)
    application = open_turn_application(directory / "runtime.db")

    async def retire(cid: str, sid: str) -> None:
        """通过生产 Turn owner 封锁根会话运行身份。"""
        await application.retire_session(derive_local_session_id("tui", {"cid": cid, "sid": sid}))

    session.bind_session_runtime_close(retire)
    capabilities = detect_terminal_capabilities(input_stream=sys.stdin, output_stream=sys.stdout)
    runtime = TuiRuntime(input_obj=create_tui_input(sys.stdin), terminal_capabilities=capabilities)
    sink = TuiApplicationSink(runtime)
    host = SimpleNamespace(
        frontend=SimpleNamespace(application=sink, runtime=runtime),
        conversation=session, lifecycle=lifecycle,
    )
    foreground = TuiForegroundTasks(runtime, host)
    dispatcher = TuiCommandDispatcher(host, runtime, SimpleNamespace(), foreground, protocol_client=FakeMindChatServer())
    await runtime.open()
    try:
        runtime.append_block(text_block("DELETE ACCEPTANCE READY"), kind="notice")
        while not lifecycle.stop_event.is_set():
            facts.stage = "input"
            facts.set_detail("pending_ids", [item.request_id for item in backend.pending()])
            facts.write()
            value = await runtime.read_message(PromptContext(model="isolated-test"))
            runtime.consume_submission_payload()
            runtime.set_turn_start_pending(False)
            action = await dispatcher.dispatch(value)
            if action is DispatchAction.MODEL_TURN:
                try:
                    await session.begin_turn()
                except ValueError:
                    runtime.append_block(text_block("Session is blocked pending deletion recovery."), kind="notice")
                else:
                    runtime.append_block(text_block(f"Accepted: {value}"), kind="notice")
            runtime.finish_command_layout()
            if action is DispatchAction.EXIT:
                break
        facts.stage = "complete"
    finally:
        await session.end(reason="exit")
        snapshot = session.take_exit_snapshot()
        await application.close(cancel_running=True)
        await runtime.close()
        if snapshot is not None:
            runtime.print_exit_summary(snapshot)
        facts.set_detail("pending_ids", [item.request_id for item in backend.pending()])
        facts.set_detail("retired", session.session_retired)
        facts.write()


def main() -> None:
    """解析显式隔离目录和本地故障服务地址。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("url")
    parser.add_argument("mode", choices=("initial", "recovery"))
    args = parser.parse_args()
    asyncio.run(run(args.directory, args.url, args.mode))


if __name__ == "__main__":
    main()
