# -*- coding: utf-8 -*-

"""审计 Agent 职责包、公开入口与组合依赖边界。"""


import ast
from tests.architecture.source_inventory import (
    PROJECT_ROOT,
    forbidden_imports as _forbidden_imports,
    parsed_source as _parsed_source,
)


def test_agent_responsibility_packages_are_physical() -> None:
    """确保 Agent Harness 的职责重组落在真实子包而非平铺或转发模块。"""
    expected_files = {
        "application/approvals/__init__.py",
        "application/approvals/amendments.py",
        "application/approvals/coordinator.py",
        "application/approvals/factory.py",
        "application/approvals/local_policy.py",
        "application/approvals/models.py",
        "application/approvals/policy.py",
        "application/approvals/presentation.py",
        "application/approvals/presenter.py",
        "application/approvals/summary.py",
        "application/agents/fork_context.py",
        "application/agents/messages.py",
        "application/agents/thread.py",
        "application/agents/views.py",
        "application/config/session_identity.py",
        "application/config/settings.py",
        "application/hooks/catalog.py",
        "application/hooks/context.py",
        "application/hooks/events.py",
        "application/hooks/models.py",
        "application/hooks/output.py",
        "application/hooks/protocol.py",
        "application/hooks/result.py",
        "application/hooks/subagent.py",
        "application/tools/__init__.py",
        "application/tools/authorization.py",
        "application/tools/catalog.py",
        "application/tools/coding_schemas.py",
        "application/tools/context.py",
        "application/tools/definitions.py",
        "application/tools/execution_results.py",
        "application/tools/execution.py",
        "application/tools/media.py",
        "application/tools/patching.py",
        "application/tools/processes.py",
        "application/tools/permissions.py",
        "application/tools/planning.py",
        "application/tools/plan_update.py",
        "application/tools/results.py",
        "application/tools/subagents.py",
        "application/turns/commands.py",
        "application/turns/compact_result.py",
        "application/turns/context.py",
        "application/turns/environment.py",
        "application/turns/exception_text.py",
        "application/turns/execution.py",
        "application/turns/projections.py",
        "application/turns/run_result.py",
        "application/turns/lifecycle.py",
        "application/turns/presentation.py",
        "application/turns/stream_boundaries.py",
        "application/turns/stream_outcome.py",
        "application/turns/transcript.py",
        "application/views/builders/__init__.py",
        "application/views/builders/approval.py",
        "application/views/builders/batch.py",
        "application/views/builders/hooks.py",
        "application/views/builders/lifecycle.py",
        "application/views/builders/patch.py",
        "application/views/builders/plan.py",
        "application/views/builders/progress.py",
        "application/views/builders/run.py",
        "application/views/builders/tools.py",
        "application/views/commands.py",
        "application/views/tool_execution.py",
        "domain/identifiers.py",
        "domain/transcripts.py",
        "domain/execution_policy/sandbox.py",
        "domain/execution_policy/requirements.py",
        "domain/permission_profiles.py",
        "ports/content.py",
        "ports/media.py",
        "ports/output.py",
        "ports/presentation.py",
        "ports/process_tools.py",
        "ports/process_lifecycle.py",
        "ports/process_resources.py",
        "harness/agents/control.py",
        "harness/agents/delivery.py",
        "harness/agents/registry.py",
        "harness/agents/runtime.py",
        "harness/execution/actor.py",
        "harness/execution/subagent_runner.py",
        "harness/execution/subagent_submission.py",
        "harness/execution/turn_finalizer.py",
        "harness/hooks/tool_lifecycle.py",
        "harness/hooks/compaction.py",
        "harness/hooks/presentation.py",
        "harness/hooks/session_lifecycle.py",
        "harness/hooks/turn_lifecycle.py",
        "harness/process_lifecycle.py",
        "harness/process_resources.py",
        "harness/tools/__init__.py",
        "harness/tools/client_calls.py",
        "harness/tools/plan_calls.py",
        "harness/tools/plan_execution.py",
        "harness/subscription/owner.py",
        "harness/sessions/conversation.py",
        "harness/sessions/loop.py",
        "harness/sessions/owner.py",
        "harness/sessions/root.py",
        "stores/agents/graph.py",
        "stores/agents/mailbox.py",
        "stores/approvals/ledger.py",
        "stores/approvals/permissions.py",
        "stores/effects/journal.py",
        "stores/sessions/__init__.py",
        "stores/sessions/history.py",
        "stores/runs/records.py",
        "stores/runs/schema.py",
        "stores/runs/store.py",
        "adapters/agents/execution.py",
        "adapters/agents/fork_context.py",
        "adapters/agents/messages.py",
        "adapters/protocol/client.py",
        "adapters/protocol/approval_events.py",
        "adapters/protocol/items.py",
        "adapters/protocol/model_events.py",
        "adapters/protocol/tool_events.py",
        "adapters/protocol/tool_results.py",
        "adapters/protocol/turn_setup.py",
        "ports/conversation.py",
    }
    missing = [
        relative
        for relative in sorted(expected_files)
        if not (PROJECT_ROOT / "agent" / relative).is_file()
    ]
    assert not missing, "reorganized Agent modules are missing: " + ", ".join(missing)

    top_level_files = {
        path.name
        for path in (PROJECT_ROOT / "agent" / "application").glob("*.py")
    }
    assert top_level_files == {"__init__.py", "services.py"}
    assert {
        path.name
        for path in (PROJECT_ROOT / "agent" / "harness").glob("*.py")
        } == {
            "__init__.py",
            "process_lifecycle.py",
            "process_resources.py",
            "workspace_runtime.py",
        }
    assert {
        path.name
        for path in (PROJECT_ROOT / "agent" / "stores").glob("*.py")
    } == {"__init__.py"}
    assert {
        path.name
        for path in (PROJECT_ROOT / "agent" / "adapters").glob("*.py")
    } == {"__init__.py"}


def test_transcript_shared_values_are_owned_by_domain() -> None:
    """确保 Transcript 值和归约归 domain、文件 adapter 归 infrastructure。"""
    target_path = PROJECT_ROOT / "agent" / "domain" / "transcripts.py"
    legacy_root = PROJECT_ROOT / "agent" / "stores" / "transcripts"
    assert target_path.is_file()
    assert not tuple(legacy_root.glob("*.py"))

    violations = _forbidden_imports(
        "agent/domain/transcripts.py",
        {"mind_app", "mind_core", "engine", "server", "infrastructure"},
    )
    assert not violations, "transcript domain crosses its boundary:\n" + (
        "\n".join(violations)
    )

    target_source = target_path.read_text(encoding="utf-8-sig")
    assert "class TranscriptEntry" in target_source
    assert "class TranscriptReplay" in target_source

    adapter_path = PROJECT_ROOT / "infrastructure" / "persistence" / "transcripts.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "history" / "transcript.py"
    assert adapter_path.is_file(), "transcript file adapter is missing"
    assert not legacy_path.exists(), "legacy transcript adapter remains"

    adapter_tree = ast.parse(
        adapter_path.read_text(encoding="utf-8-sig")
    )
    adapter_classes = {
        node.name
        for node in adapter_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert adapter_classes == {
        "TranscriptReader",
        "TranscriptWriter",
        "ConversationTranscriptStore",
    }


def test_subagent_fork_context_adapter_uses_explicit_transcript_reader() -> None:
    """确保 fork 上下文 adapter 不实例化具体文件存储。"""
    target_path = PROJECT_ROOT / "agent" / "adapters" / "agents" / "fork_context.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "context.py"
    assert target_path.is_file(), "fork context adapter is missing"
    assert not legacy_path.exists(), "legacy fork context adapter remains"

    violations = _forbidden_imports(
        "agent/adapters/agents",
        {"infrastructure", "mind_app", "mind_core", "engine", "server"},
    )
    assert not violations, "fork adapter owns a concrete transcript store:\n" + (
        "\n".join(violations)
    )

    source = target_path.read_text(encoding="utf-8-sig")
    assert "transcript_entries_for" in source


def test_session_history_store_is_owned_by_stores() -> None:
    """确保 Session 游标存储不读取 legacy history 或基础设施路径。"""
    target_path = PROJECT_ROOT / "agent" / "stores" / "sessions" / "history.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "history" / "store.py"
    assert target_path.is_file(), "session history store is missing"
    assert not legacy_path.exists(), "legacy session history store remains"

    violations = _forbidden_imports(
        "agent/stores/sessions",
        {"mind_app", "mind_core", "engine", "server", "infrastructure"},
    )
    assert not violations, "session stores cross their boundary:\n" + (
        "\n".join(violations)
    )

    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"))
    classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "ConversationHistoryStore" in classes

    legacy_files = (
        "application/agent_thread.py",
        "application/agent_messages.py",
        "application/agent_views.py",
        "application/fork_context.py",
        "application/commands.py",
        "application/execution.py",
        "application/turn_execution.py",
        "application/environment.py",
        "application/compact_result.py",
        "application/run_result.py",
        "application/stream_outcome.py",
        "application/projections.py",
        "application/hook_catalog.py",
        "application/hook_context.py",
        "application/hook_events.py",
        "application/hook_models.py",
        "application/hook_output.py",
        "application/hook_protocol.py",
        "application/hook_result.py",
        "application/subagent_hooks.py",
        "application/settings.py",
        "application/session_identity.py",
        "harness/agent_control.py",
        "harness/agent_delivery.py",
        "harness/agent_registry.py",
        "harness/run_actor.py",
        "harness/subagent_runner.py",
        "harness/subagent_submission.py",
        "harness/session_loop.py",
        "harness/session_owner.py",
        "stores/agent_graph.py",
        "stores/agent_mailbox.py",
        "stores/approval_ledger.py",
        "stores/permission_grants.py",
        "stores/effect_journal.py",
        "stores/run_store.py",
        "stores/_run_records.py",
        "stores/_run_schema.py",
        "adapters/protocol_client.py",
        "adapters/item_reducer.py",
        "adapters/agent_messages.py",
        "adapters/subagent_execution.py",
    )
    present = [
        relative
        for relative in legacy_files
        if (PROJECT_ROOT / "agent" / relative).exists()
    ]
    assert not present, "legacy flat Agent modules still exist: " + ", ".join(present)


def test_agent_application_public_api_is_minimal() -> None:
    """application 包只公开跨入口用例，不重新聚合领域和基础端口。"""
    path = PROJECT_ROOT / "agent" / "application" / "__init__.py"
    tree = _parsed_source(path)
    exported: object | None = None
    imported_modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            exported = ast.literal_eval(node.value)
        elif isinstance(node, ast.ImportFrom) and node.level == 1:
            imported_modules.add(node.module or "")

    assert exported == (
        "RuntimeServices",
        "SubmitTurnResult",
        "TurnApplication",
        "submit_turn",
    )
    assert imported_modules == {"services", "turns.commands"}


def test_runtime_services_keep_model_control_and_subscription_wiring_explicit(
) -> None:
    """确保组合服务不再由 CLI 或订阅宿主动态发现。"""
    services_path = PROJECT_ROOT / "agent" / "application" / "services.py"
    services_source = services_path.read_text(encoding="utf-8-sig")
    services_tree = ast.parse(services_source, filename=str(services_path))
    runtime_services = next(
        node
        for node in services_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "RuntimeServices"
    )
    service_fields = {
        node.target.id: ast.unparse(node.annotation)
        for node in runtime_services.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert service_fields["model_capability"] == "ModelCapability"
    assert service_fields["protocol_client"] == "ProtocolCommandClient"
    assert "class SubscriptionRuntimeBuilder(typing.Protocol)" in services_source

    subscription_port_source = (
        PROJECT_ROOT / "agent" / "ports" / "subscription.py"
    ).read_text(encoding="utf-8-sig")
    assert "SubscriptionRuntimeBuilder" not in subscription_port_source

    cli_source = (
        PROJECT_ROOT / "frontends" / "cli" / "bootstrap.py"
    ).read_text(encoding="utf-8-sig")
    assert "runtime_services: RuntimeServices | None" not in cli_source
    assert "getattr(runtime_services" not in cli_source
    assert "runtime_services.model_capability" not in cli_source
    assert "runtime_services.protocol_client" in cli_source

    composition_source = (PROJECT_ROOT / "mind.py").read_text(
        encoding="utf-8-sig"
    )
    assert 'getattr(host, "runtime_services"' not in composition_source
    assert "runtime_services: RuntimeServices" in composition_source


def test_agent_composition_has_no_infrastructure_imports() -> None:
    """组合契约接收基础设施适配器，不在 agent 包内反向导入实现。"""
    path = PROJECT_ROOT / "agent" / "composition.py"
    tree = _parsed_source(path)
    violations: list[str] = []
    for node in ast.walk(tree):
        imported: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            imported = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported = (node.module or "",)
        if any(
            module == "infrastructure"
            or module.startswith("infrastructure.")
            for module in imported
        ):
            violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert not violations, (
        "agent composition imports infrastructure: "
        + ", ".join(violations)
    )


def test_agent_capabilities_depend_only_on_protocol_transport() -> None:
    violations = _forbidden_imports(
        "agent/capabilities",
        {
            "applications",
            "backend",
            "engine",
            "frontends",
            "mind_app",
            "mind_core",
            "server",
        },
    )

    assert not violations, "agent capabilities cross adapter boundaries:\n" + (
        "\n".join(violations)
    )


def test_root_turn_command_adapter_is_controller_independent() -> None:
    """确保主动 Turn 命令映射由 agent adapter 持有且不依赖旧控制器。"""
    adapter_path = PROJECT_ROOT / "agent" / "adapters" / "turns" / "root.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "root.py"

    assert adapter_path.is_file(), "root turn command adapter is missing"
    assert not legacy_path.exists(), "legacy root Turn runtime remains"

    violations = _forbidden_imports(
        "agent/adapters/turns",
        {
            "backend",
            "engine",
            "infrastructure",
            "mind_app",
            "mind_core",
            "observability",
            "server",
        },
    )
    assert not violations, "root turn adapter crosses boundaries:\n" + "\n".join(
        violations
    )


def test_foreground_turn_orchestration_uses_injected_presentation_port() -> None:
    """确保前台轮次编排只消费注入的展示端口。"""
    harness_path = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "root_runner.py"
    )
    application_path = (
        PROJECT_ROOT
        / "agent"
        / "application"
        / "turns"
        / "foreground.py"
    )
    legacy_terminal_path = (
        PROJECT_ROOT
        / "frontends"
        / "terminal"
        / "turn_lifecycle.py"
    )

    assert application_path.is_file(), "foreground Turn use case is missing"
    assert not legacy_terminal_path.exists(), (
        "legacy terminal turn lifecycle adapter remains"
    )
    harness_tree = ast.parse(
        harness_path.read_text(encoding="utf-8-sig"),
        filename=str(harness_path),
    )
    harness_functions = {
        node.name
        for node in harness_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "run_foreground_turn" not in harness_functions

    application_tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    application_functions = {
        node.name
        for node in application_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert application_functions == {"run_foreground_turn"}
    application_violations = _forbidden_imports(
        "agent/application/turns",
        {"frontends", "infrastructure", "mind_app", "mind_core", "server"},
    )
    assert not application_violations, (
        "foreground Turn use case crosses application boundary:\n"
        + "\n".join(application_violations)
    )

    application_classes = {
        node.name
        for node in application_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert application_classes == {
        "ApplicationTurnForegroundLifecycle",
    }
