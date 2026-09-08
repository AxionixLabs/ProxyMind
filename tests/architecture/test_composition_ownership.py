# -*- coding: utf-8 -*-

"""审计进程组合根、可观测性和资源生命周期的所有权。"""


import ast
from tests.architecture.source_inventory import (
    PROJECT_ROOT,
    all_python_sources as _all_python_sources,
    forbidden_module_imports as _forbidden_module_imports,
    parsed_source as _parsed_source,
)


def test_agent_application_does_not_load_concrete_composition() -> None:
    violations = _forbidden_module_imports(
        "agent/application",
        {
            "agent.capabilities",
            "agent.composition",
            "agent.harness",
            "agent.stores",
        },
    )

    assert not violations, "agent application loads concrete adapters:\n" + (
        "\n".join(violations)
    )


def test_legacy_application_does_not_import_model_transport() -> None:
    violations = _forbidden_module_imports(
        "mind_app",
        {"protocol.client.chat"},
    )

    assert not violations, "legacy application imports model transport:\n" + (
        "\n".join(violations)
    )


def test_legacy_application_uses_application_or_owned_state_entry() -> None:
    """限制旧应用只能使用 application 用例或明确归属的 state store。"""
    allowed_modules = {
        "agent.application",
        "agent.application.hooks.catalog",
        "agent.application.hooks.events",
        "agent.application.hooks.protocol",
        "agent.application.hooks.models",
        "agent.application.hooks.output",
        "agent.application.hooks.result",
        "agent.application.hooks.subagent",
        "agent.application.agents.thread",
        "agent.application.agents.views",
        "agent.application.agents.messages",
        "agent.application.approvals.amendments",
        "agent.application.approvals.coordinator",
        "agent.application.approvals.factory",
        "agent.application.approvals.local_policy",
        "agent.application.approvals.models",
        "agent.application.approvals.policy",
        "agent.application.approvals.presentation",
        "agent.application.approvals.presenter",
        "agent.application.approvals.summary",
        "agent.application.turns.context",
        "agent.application.turns.commands",
        "agent.application.turns.environment",
        "agent.application.turns.exception_text",
        "agent.application.turns.compact_result",
        "agent.application.turns.run_result",
        "agent.application.turns.stream_outcome",
        "agent.application.turns.projections",
        "agent.application.turns.lifecycle",
        "agent.application.turns.presentation",
        "agent.application.turns.stream_boundaries",
        "agent.application.turns.transcript",
        "agent.application.hooks.context",
        "agent.application.turns.execution",
        "agent.application.turns.foreground",
        "agent.application.agents.fork_context",
        "agent.application.views",
        "agent.application.views.contracts",
        "agent.application.views.commands",
        "agent.application.views.tool_display",
        "agent.application.views.tool_execution",
        "agent.application.views.builders.approval",
        "agent.application.views.builders.batch",
        "agent.application.views.builders.lifecycle",
        "agent.application.views.builders.patch",
        "agent.application.views.builders.plan",
        "agent.application.views.builders.progress",
        "agent.application.views.builders.run",
        "agent.application.views.builders.tools",
        "agent.application.config.settings",
        "agent.application.config.session_identity",
        "agent.application.tools.catalog",
        "agent.application.tools.authorization",
        "agent.application.tools.coding_schemas",
        "agent.application.tools.context",
        "agent.application.tools.definitions",
        "agent.application.tools.execution_results",
        "agent.application.tools.execution",
        "agent.application.tools.media",
        "agent.application.tools.patching",
        "agent.application.tools.processes",
        "agent.application.tools.permissions",
        "agent.application.tools.planning",
        "agent.application.tools.plan_update",
        "agent.application.tools.results",
        "agent.application.tools.subagents",
        "agent.ports.media",
        "agent.domain.hooks",
        "agent.domain.identifiers",
        "agent.domain.permission_profiles",
        "agent.domain.policies",
        "agent.domain.hook_trust",
        "agent.domain.hook_matching",
        "agent.domain.agents",
        "agent.domain.tool_policy",
        "agent.domain.execution_policy",
        "agent.ports",
        "agent.ports.frontend",
        "agent.ports.presentation",
        "agent.ports.process_lifecycle",
        "agent.ports.subagents",
        "agent.ports.agent_messages",
        "agent.ports.transcript",
        "agent.ports.conversation",
        "agent.domain.transcripts",
        "agent.adapters.agents.messages",
        "agent.adapters.agents.execution",
        "agent.adapters.agents.fork_context",
        "agent.adapters.protocol.model_events",
        "agent.adapters.protocol.approval_events",
        "agent.adapters.protocol.tool_events",
        "agent.adapters.protocol.tool_results",
        "agent.adapters.protocol.turn_setup",
        "agent.adapters.protocol.subagent_stream",
        "agent.adapters.turns.root",
        "agent.harness.agents.control",
        "agent.harness.agents.delivery",
        "agent.harness.agents.registry",
        "agent.harness.agents.runtime",
        "agent.harness.hooks.runtime",
        "agent.harness.hooks.scope",
        "agent.harness.hooks.compaction",
        "agent.harness.hooks.presentation",
        "agent.harness.hooks.session_lifecycle",
        "agent.harness.hooks.tool_lifecycle",
        "agent.harness.hooks.turn_lifecycle",
        "agent.harness.mcp.owner",
        "agent.harness.sessions.conversation",
        "agent.harness.sessions.root",
        "agent.harness.subscription.owner",
            "agent.harness.execution.subagent_runner",
            "agent.harness.execution.subagent_submission",
            "agent.harness.execution.resources",
            "agent.harness.execution.turn_runner",
        "agent.harness.execution.turn_finalizer",
        "agent.harness.tools.client_calls",
        "agent.harness.tools.plan_calls",
        "agent.ports.subscription",
        "agent.protocol.json_value",
        "agent.protocol",
        "agent.stores.approvals.ledger",
        "agent.stores.approvals.permissions",
        "agent.stores.agents.mailbox",
        "agent.stores.agents.graph",
        "agent.stores.sessions",
        "agent.stores",
    }
    violations: list[str] = []
    package_root = PROJECT_ROOT / "mind_app"

    for path in package_root.rglob("*.py"):
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = (node.module or "",)

            for module in imported:
                if module == "agent" or module.startswith("agent."):
                    if module not in allowed_modules:
                        relative = path.relative_to(PROJECT_ROOT)
                        violations.append(
                            f"{relative}:{node.lineno} -> {module}"
                        )

    assert not violations, (
        "legacy application imports an unowned agent boundary:\n"
        + "\n".join(violations)
    )


def test_product_logging_enters_observability_boundary() -> None:
    """禁止业务包重新拥有标准 logging logger。"""
    violations: list[str] = []
    excluded_parts = {"tests", "venv", "codex-main", "backend", "observability"}

    for path in _all_python_sources():
        relative_parts = set(path.relative_to(PROJECT_ROOT).parts)
        if relative_parts & excluded_parts:
            continue

        tree = _parsed_source(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "logging" for alias in node.names):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> logging")
                if any(alias.name == "loguru" for alias in node.names):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> loguru")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if (node.module or "").partition(".")[0] == "logging":
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> logging")
                if (node.module or "").partition(".")[0] == "loguru":
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> loguru")

            if isinstance(node, ast.Assign):
                targets = [*node.targets]
                if any(
                    isinstance(target, ast.Name) and target.id == "_LOGGER"
                    for target in targets
                ):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> _LOGGER")

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
            ):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> getLogger")

    assert not violations, "production logging bypasses observability:\n" + "\n".join(violations)


def test_run_report_has_observability_ownership() -> None:
    """确保报告文件 sink 不再由 mind_app 持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "reporting.py"
    assert not legacy_path.is_file(), "legacy RunReport module still exists"

    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.reporting" or module.startswith("mind_app.reporting."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy reporting imports remain:\n" + "\n".join(violations)


def test_event_report_lifecycle_has_protocol_client_ownership() -> None:
    """确保事件报告生命周期不再由 runtime turns 持有。"""
    legacy_path = (
        PROJECT_ROOT
        / "mind_app"
        / "runtime"
        / "turns"
        / "event_reporting.py"
    )
    client_path = PROJECT_ROOT / "protocol" / "client" / "reports.py"

    assert not legacy_path.is_file(), "legacy event reporting source still exists"
    assert client_path.is_file(), "protocol client report lifecycle is missing"

    legacy_modules = {
        "mind_app.runtime.turns.event_reporting",
    }
    violations: list[str] = []
    for path in _all_python_sources():
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy event reporting imports remain:\n" + "\n".join(
        violations
    )


def test_hook_registry_is_composed_at_the_process_root() -> None:
    """确保入口只消费 Hook registry port，不直接装配具体实现。"""
    entry_paths = (
        PROJECT_ROOT / "composition.py",
        PROJECT_ROOT / "frontends" / "cli" / "bootstrap.py",
        PROJECT_ROOT / "frontends" / "mcp" / "server.py",
    )
    violations: list[str] = []
    for path in entry_paths:
        tree = _parsed_source(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in {
                    "mind_app.runtime.hooks.registry",
                    "agent.harness.hooks.registry",
                }:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} imports concrete HookRegistry"
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "HookRegistry"
            ):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} constructs concrete HookRegistry"
                )

    assert not violations, "Hook registry construction escaped composition root:\n" + (
        "\n".join(violations)
    )


def test_application_host_uses_owned_resources_without_legacy_facades() -> None:
    """确保具体宿主只组合 owner，且历史应用包已经物理退役。"""
    host_path = PROJECT_ROOT / "composition.py"
    resource_path = (
        PROJECT_ROOT / "agent" / "harness" / "process_resources.py"
    )
    port_path = PROJECT_ROOT / "agent" / "ports" / "process_resources.py"
    assert host_path.is_file()
    assert resource_path.is_file()
    assert port_path.is_file()
    assert not tuple((PROJECT_ROOT / "mind_app").rglob("*.py"))

    host_tree = ast.parse(
        host_path.read_text(encoding="utf-8-sig"),
        filename=str(host_path),
    )
    host = next(
        node
        for node in host_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ApplicationHost"
    )
    methods = {
        node.name
        for node in host.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assigned_attributes = {
        node.attr
        for node in ast.walk(host)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
    }
    assert "close_runtime_resources" not in methods
    assert "resources" in assigned_attributes
    assert not {
        "level",
        "power",
        "remote",
        "src_opera_place",
        "src_total_place",
    } & assigned_attributes

    resource_source = resource_path.read_text(encoding="utf-8-sig")
    assert "class ProcessResourceOwner" in resource_source
    assert "self._next_step" in resource_source
    assert "asyncio.Lock()" in resource_source
    assert "class ProcessResourcePort(typing.Protocol)" in (
        port_path.read_text(encoding="utf-8-sig")
    )


def test_process_lifecycle_and_frontend_activity_are_composed_once() -> None:
    """确保进程生命周期和前端活动由组合根持有且旧 facade 不会回流。"""
    composition_path = PROJECT_ROOT / "mind.py"
    host_path = PROJECT_ROOT / "composition.py"
    composition_source = composition_path.read_text(encoding="utf-8-sig")
    host_source = host_path.read_text(encoding="utf-8-sig")

    assert "from agent.harness.process_lifecycle import ProcessLifecycle" in (
        composition_source
    )
    assert "lifecycle=ProcessLifecycle()" in composition_source
    assert "activity=activity" in composition_source
    assert "ProcessLifecycle(" not in host_source
    assert "AsyncAnimManager" not in host_source

    forbidden_facades = {
        "freeze_anim",
        "start_anim",
        "start_compact_anim",
        "start_external_mcp_anim",
        "start_inbuild_startup_anim",
        "start_upload_anim",
        "stop_anim",
        "task_event",
    }
    violations: list[str] = []
    production_roots = (
        PROJECT_ROOT / "agent",
        PROJECT_ROOT / "frontends",
        PROJECT_ROOT / "infrastructure",
        PROJECT_ROOT / "protocol",
    )
    for root in production_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(
                path.read_text(encoding="utf-8-sig"),
                filename=str(path),
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in forbidden_facades:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {node.attr}"
                    )

    assert not violations, "legacy lifecycle facades remain:\n" + "\n".join(
        violations
    )


def test_root_conversation_ownership_is_split_by_responsibility() -> None:
    """确保根会话编排与本地历史持久化不再由 Controller 混合拥有。"""
    session_path = (
        PROJECT_ROOT / "agent" / "harness" / "sessions" / "root.py"
    )
    history_path = (
        PROJECT_ROOT
        / "infrastructure"
        / "persistence"
        / "conversation_history.py"
    )
    port_path = PROJECT_ROOT / "agent" / "ports" / "conversation.py"
    assert session_path.is_file()
    assert history_path.is_file()
    assert port_path.is_file()
    assert not (
        PROJECT_ROOT / "agent" / "harness" / "sessions" / "history.py"
    ).exists()

    session_source = session_path.read_text(encoding="utf-8-sig")
    assert "class RootConversationSession" in session_source
    assert "ConversationHistoryPort" in session_source
    assert "ConversationHistoryStore" not in session_source
    assert "infrastructure" not in session_source

    history_source = history_path.read_text(encoding="utf-8-sig")
    assert "class LocalConversationHistory" in history_source
    assert "ConversationHistoryStore" in history_source
    assert "TranscriptEntry" in history_source

    port_source = port_path.read_text(encoding="utf-8-sig")
    assert "class ConversationHistoryPort" in port_source
    assert "class RootConversationPort" in port_source
