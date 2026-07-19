# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path

from mind_app.approval.models import ApprovalDecisionValue
from mind_app.interaction import PromptContext
from mind_app.interaction import legacy as legacy_module
from mind_app.interaction.legacy import LegacyInteraction
from mind_app.interaction.noninteractive import NonInteractiveInteraction
from mind_core.prompting import PromptToolkitBox

ROOT = Path(__file__).resolve().parents[1]


class FakePromptBox(PromptToolkitBox):
    """记录主输入框接收到的上下文字段。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def prompt_async(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "message"


def test_legacy_interaction_delegates_main_prompt_and_approval(monkeypatch) -> None:
    """默认交互实现保持主输入和审批委托参数。"""
    prompt_box = FakePromptBox()
    approvals: list[dict] = []

    async def fake_approval_prompt(approval: dict) -> ApprovalDecisionValue:
        approvals.append(approval)
        return "acceptForSession"

    monkeypatch.setattr(
        legacy_module,
        "prompt_tool_approval_decision",
        fake_approval_prompt,
    )
    interaction = LegacyInteraction(prompt_box)
    context = PromptContext(
        mode="fast",
        model="model-1",
        workspace_label="workspace",
        access_label="safe",
        exec_status_label="2 running",
    )
    approval = {
        "id": "approval-1",
        "tool": "shell_command",
        "arguments": {"command": "printf hello"},
    }

    async def run() -> tuple[str, str]:
        message = await interaction.read_message(context)
        decision = await interaction.request_approval(approval)
        return message, decision

    assert asyncio.run(run()) == ("message", "acceptForSession")
    assert prompt_box.calls == [{
        "mode": "fast",
        "model": "model-1",
        "workspace_label": "workspace",
        "access_label": "safe",
        "exec_status_label": "2 running",
    }]
    assert approvals == [approval]


def test_runtime_uses_interaction_port_instead_of_prompt_implementations() -> None:
    """主循环和流式循环不再直接依赖具体输入实现。"""
    repl_source = (ROOT / "mind_app/modes/repl.py").read_text(encoding="utf-8")
    stream_source = (ROOT / "mind_app/modes/stream.py").read_text(encoding="utf-8")
    mind_source = (ROOT / "mind_app/mind_core.py").read_text(encoding="utf-8")

    assert "mind.prompt_box" not in repl_source
    assert "prompt_tool_approval_decision" not in stream_source
    assert "approval_input_func" not in stream_source
    assert "self.prompt_box" not in mind_source
    assert "self.frontend: Frontend" in mind_source
    assert "NonInteractiveInteraction" not in mind_source
    assert "LegacyInteraction" not in mind_source


def test_noninteractive_interaction_declines_approval() -> None:
    """非交互运行不会打开终端审批菜单。"""
    interaction = NonInteractiveInteraction()

    decision = asyncio.run(interaction.request_approval({"tool": "shell_command"}))

    assert decision == "decline"


if __name__ == '__main__':
    pass
