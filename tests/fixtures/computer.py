import typing
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    Mock,
)

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.approvals.legacy import DomainApprovalCoordinator
from agent.application.config.settings import FeatureSettings
from agent.application.hooks.models import (
    HookVisibleToolResult,
    ToolCallRunResult,
)
from agent.application.turns.context import (
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from agent.composition import open_effect_journal
from agent.domain.policies import preset_permissions
from agent.harness.sessions.desktop import DesktopRuntime
from agent.harness.tools.client_calls import (
    ClientToolCallOutcome,
    ClientToolCallResult,
    ClientToolCallRunner,
    ClientToolOperation,
)
from agent.ports.desktop import DesktopSessionFactory
from agent.stores.approvals.facts import SQLiteApprovalFactStore
from agent.stores.approvals.grants import InMemorySessionGrantStore
from infrastructure.mcp.composite_session import CompositeToolSession
from infrastructure.mcp.local_tool_factory import build_client_tool_registry
from infrastructure.mcp.tool_execution import McpToolExecutionAdapter
from infrastructure.platform.images import FileImageReader
from infrastructure.sidecars.javascript.provider import JavaScriptSidecarProvider
from mind import (
    create_javascript_provider,
    create_workspace_coding,
)


@dataclass
class ComputerHarness:
    runner: ClientToolCallRunner
    turn: TurnContext
    desktop: DesktopRuntime
    javascript: JavaScriptSidecarProvider
    interaction: Mock
    hooks: Mock
    approvals: DomainApprovalCoordinator

    async def execute(self, code: str, call_id: str) -> ClientToolCallOutcome:
        """通过生产工具执行器进入真实 JS 内核及嵌套工具链。"""
        return await self.runner.execute(ToolInvocation(
            turn=self.turn, name="js_repl", call_id=call_id,
            arguments={"code": code, "timeout_ms": 10000},
        ), use_coding_trace=True)


async def allow_operation(invocation: ToolInvocation, operation: ClientToolOperation) -> ToolCallRunResult[ClientToolCallResult]:
    """记录 Hook 边界而不在桌面测试中运行用户脚本。"""
    result = await operation(invocation)
    return ToolCallRunResult(allowed=True, value=result.value, visible_result=HookVisibleToolResult(
        ok=result.snapshot.ok, text=result.snapshot.text, fields=result.snapshot.fields,
    ))


@asynccontextmanager
async def computer_harness(
    directory: Path, factory: DesktopSessionFactory, *, enabled: bool = True, decision: str = "accept",
) -> typing.AsyncIterator[ComputerHarness]:
    """组合真实注册表、审批账本、Harness 和 Node，仅替换交互决定与原生工厂。"""
    directory.mkdir(parents=True, exist_ok=True)
    interaction = Mock(approval_source="user", begin_approval_session=AsyncMock(),
        end_approval_session=AsyncMock(), present_approval=AsyncMock(return_value=decision))
    approvals = DomainApprovalCoordinator(ApprovalCoordinator(interaction),
        fact_store=SQLiteApprovalFactStore(directory / "approvals.db"), grant_store=InMemorySessionGrantStore())
    desktop = DesktopRuntime(factory)
    coding = create_workspace_coding(root=directory, application_layout=None)
    javascript = create_javascript_provider(workspace_root=directory, application_layout=None)
    registry = build_client_tool_registry(coding, javascript=javascript, desktop=desktop,
        image_reader=FileImageReader(directory), approval_coordinator=approvals,
        features=FeatureSettings(js_repl=True, computer=enabled))
    session = CompositeToolSession(client_registry=registry)
    turn = TurnContext.create(agent=AgentContext.root("sid_computer"), cid="cid_computer", sid="sid_computer",
        source="test", pref_config={}, cwd=str(directory), permissions=preset_permissions("auto"), turn_id="turn_computer")
    hooks = Mock(run_invocation=AsyncMock(side_effect=allow_operation))
    runner = ClientToolCallRunner(session=session, output_control=Mock(), presentation=Mock(emit=AsyncMock()),
        tools=[{"name": name, "meta": {"client_builtin": True}} for name in
               ("js_repl", "computer") if name != "computer" or enabled],
        pref_config={}, tool_call_coordinator=hooks, tool_execution=McpToolExecutionAdapter(),
        activity=Mock(tool_started=AsyncMock(), tool_completed=AsyncMock()),
        effect_journal=open_effect_journal(directory / "effects.db", cid=turn.cid, sid=turn.sid))
    try:
        yield ComputerHarness(runner, turn, desktop, javascript, interaction, hooks, approvals)
    finally:
        try:
            await javascript.close()
        finally:
            try:
                await desktop.close()
            finally:
                await approvals.close()
                await coding.close()
