# -*- coding: utf-8 -*-

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


def _forbidden_imports(
    package: str,
    forbidden_roots: set[str],
) -> list[str]:
    package_root = PROJECT_ROOT / package
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = (node.module or "",)

            for module in imported:
                root = module.partition(".")[0]
                if root in forbidden_roots:
                    relative = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{relative}:{node.lineno} -> {module}")

    return violations


def _forbidden_module_imports(
    package: str,
    forbidden_modules: set[str],
) -> list[str]:
    package_root = PROJECT_ROOT / package
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = (node.module or "",)

            for module in imported:
                if any(
                    module == forbidden
                    or module.startswith(f"{forbidden}.")
                    for forbidden in forbidden_modules
                ):
                    relative = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{relative}:{node.lineno} -> {module}")

    return violations


def test_transport_protocol_package_is_independent() -> None:
    violations = _forbidden_imports(
        "protocol",
        {"backend", "engine", "mind_app", "mind_core", "server"},
    )

    assert not violations, "protocol crosses its package boundary:\n" + "\n".join(
        violations
    )


def test_protocol_layers_remain_one_directional() -> None:
    """约束 wire SDK 的 schema、transport、client 依赖方向。"""
    schema_violations = _forbidden_module_imports(
        "protocol/schema",
        {"protocol.client", "protocol.transport", "agent", "mind_app", "mind_core", "engine"},
    )
    transport_violations = _forbidden_module_imports(
        "protocol/transport",
        {"protocol.client", "agent", "mind_app", "mind_core", "engine"},
    )

    violations = [*schema_violations, *transport_violations]
    assert not violations, "protocol layer direction is invalid:\n" + "\n".join(
        violations
    )


def test_historical_source_packages_are_retired() -> None:
    """确保阶段 5 已退役包不能重新承载生产源码。"""
    retired_packages = (
        "engine",
        "mind_app",
        "mind_core",
        "mind_nova",
    )
    remaining = tuple(
        path.relative_to(PROJECT_ROOT).as_posix()
        for package in retired_packages
        for path in (PROJECT_ROOT / package).rglob("*.py")
    )

    assert not remaining, "retired package sources returned:\n" + "\n".join(
        remaining
    )


def test_packaged_backend_is_self_contained() -> None:
    violations = _forbidden_imports(
        "backend",
        {"engine", "mind_app", "mind_core", "protocol", "server"},
    )

    assert not violations, "backend imports application code:\n" + "\n".join(
        violations
    )


def test_agent_harness_core_does_not_import_legacy_packages() -> None:
    legacy_forbidden = {
        "applications",
        "backend",
        "engine",
        "frontends",
        "mind_app",
        "mind_core",
        "server",
    }
    protocol_forbidden = {*legacy_forbidden, "protocol"}
    violations = [
        *_forbidden_imports("agent/protocol", protocol_forbidden),
        *_forbidden_imports("agent/domain", legacy_forbidden),
        *_forbidden_imports("agent/ports", legacy_forbidden),
        *_forbidden_imports("agent/harness", legacy_forbidden),
        *_forbidden_imports("agent/application", legacy_forbidden),
        *_forbidden_imports(
            "agent/stores",
            {*legacy_forbidden, "infrastructure"},
        ),
    ]

    assert not violations, "agent harness imports legacy code:\n" + "\n".join(
        violations
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
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
        "FrontendTurnAnimation",
    }


def test_infrastructure_does_not_depend_on_legacy_runtime() -> None:
    """基础设施实现不能重新依赖已进入退役流程的业务包。"""
    violations = _forbidden_imports(
        "infrastructure",
        {"engine", "mind_app", "mind_core", "server"},
    )

    assert not violations, "infrastructure imports legacy runtime:\n" + (
        "\n".join(violations)
    )


def test_retired_packages_have_no_production_imports() -> None:
    """已退役的历史包不能从生产代码重新进入导入图。"""
    ignored_roots = {
        "backend",
        "codex-main",
        "schematic",
        "tests",
        "venv",
        "website",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        relative = path.relative_to(PROJECT_ROOT)
        if relative.parts and relative.parts[0] in ignored_roots:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in {"engine", "mind_nova"}:
                    violations.append(f"{relative}:{node.lineno} -> {module}")

    assert not violations, "production imports retired package:\n" + "\n".join(
        violations
    )


def test_migrated_configuration_modules_have_no_legacy_sources_or_imports() -> None:
    """确保已迁移的配置职责不会回到 mind_core 旧路径。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_core" / "agent_config.py",
        PROJECT_ROOT / "mind_core" / "feature_config.py",
        PROJECT_ROOT / "mind_core" / "provider_config.py",
    )
    assert not any(path.exists() for path in legacy_paths), (
        "migrated configuration source still exists: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.exists()
        )
    )

    legacy_modules = {
        "mind_core.agent_config",
        "mind_core.feature_config",
        "mind_core.provider_config",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy configuration imports remain:\n" + "\n".join(violations)
    )


def test_skills_resources_have_no_legacy_package_or_imports() -> None:
    """确保技能资源发现不回到 mind_core。"""
    legacy_root = PROJECT_ROOT / "mind_core" / "skills"
    assert not legacy_root.exists(), "legacy skills package still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.skills" or module.startswith("mind_core.skills."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy skills imports remain:\n" + "\n".join(violations)


def test_project_trust_has_no_legacy_source_or_imports() -> None:
    """确保项目边界信任事实只由配置基础设施解析。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "project_trust.py"
    assert not legacy_path.exists(), "legacy project trust source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.project_trust":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy project trust imports remain:\n" + "\n".join(violations)


def test_permission_policy_has_no_legacy_source_or_imports() -> None:
    """确保权限策略只由 Harness domain 持有。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "permissions.py"
    assert not legacy_path.exists(), "legacy permission policy source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.permissions":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy permission policy imports remain:\n" + "\n".join(violations)


def test_hook_modules_have_no_legacy_sources_or_imports() -> None:
    """确保 Hook domain 和发现基础设施不回到 mind_core。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_core" / "hooks.py",
        PROJECT_ROOT / "mind_core" / "hook_trust.py",
        PROJECT_ROOT / "mind_core" / "hook_discovery.py",
    )
    assert not any(path.exists() for path in legacy_paths), (
        "legacy hook source still exists: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.exists()
        )
    )

    legacy_modules = {
        "mind_core.hooks",
        "mind_core.hook_trust",
        "mind_core.hook_discovery",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, "legacy hook imports remain:\n" + "\n".join(violations)


def test_service_config_has_no_legacy_source_or_imports() -> None:
    """确保服务域名配置不反向依赖 mind_core。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "service_config.py"
    assert not legacy_path.exists(), "legacy service config source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.service_config":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy service config imports remain:\n" + "\n".join(violations)


def test_preference_projection_has_no_legacy_source_or_imports() -> None:
    """确保偏好投影和状态不回到 mind_core。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "preference.py"
    assert not legacy_path.exists(), "legacy preference source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.preference":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy preference imports remain:\n" + "\n".join(violations)


def test_config_store_has_no_legacy_source_or_imports() -> None:
    """确保 TOML 存储只由配置基础设施持有。"""
    legacy_path = PROJECT_ROOT / "mind_core" / "config_store.py"
    assert not legacy_path.exists(), "legacy config store source still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_core.config_store":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy config store imports remain:\n" + "\n".join(violations)


def test_configuration_layers_have_no_legacy_sources_or_imports() -> None:
    """确保配置 schema、分层和会话只由基础设施配置包持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_core" / "config.py",
        PROJECT_ROOT / "mind_core" / "config_layers.py",
        PROJECT_ROOT / "mind_core" / "config_session.py",
    )
    assert not any(path.exists() for path in legacy_paths), (
        "legacy configuration sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.exists()
        )
    )

    legacy_modules = {
        "mind_core.config",
        "mind_core.config_layers",
        "mind_core.config_session",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy configuration imports remain:\n" + "\n".join(violations)
    )


def test_runtime_paths_have_no_legacy_application_module() -> None:
    """确保用户数据目录和运行时数据库路径由配置基础设施持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "paths.py"
    assert not legacy_path.is_file(), "legacy application paths module still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.paths" or module.startswith("mind_app.paths."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy application path imports remain:\n" + "\n".join(violations)


def test_runtime_asset_and_attachment_boundaries_have_no_legacy_sources() -> None:
    """确保升级资产和附件输入状态不再挂在应用根目录。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "assets.py",
        PROJECT_ROOT / "mind_app" / "attach.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy asset/attachment sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.assets",
        "mind_app.attach",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module in legacy_modules or any(
                    module.startswith(f"{legacy}.") for legacy in legacy_modules
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy asset/attachment imports remain:\n" + "\n".join(violations)
    )


def test_mcp_runtime_has_no_legacy_root_package_or_imports() -> None:
    """确保 MCP 配置、连接、会话和结果工具统一归入 runtime/mcp。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "mcp"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy MCP sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.mcp" or module.startswith("mind_app.mcp."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy MCP imports remain:\n" + "\n".join(violations)


def test_mcp_stdio_adapter_is_owned_by_frontends() -> None:
    """确保 stdio MCP 入站适配器已迁入前端边界。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "server.py"
    target_path = PROJECT_ROOT / "frontends" / "mcp" / "server.py"
    assert not legacy_path.is_file(), "legacy MCP stdio adapter still exists"
    assert target_path.is_file(), "frontend MCP stdio adapter is missing"

    tree = ast.parse(
        target_path.read_text(encoding="utf-8-sig"),
        filename=str(target_path),
    )
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "MindMcpRuntime",
        "create_mind_mcp_server",
        "run_mind_mcp_server",
    } <= definitions

    target_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            target_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            target_modules.append(node.module or "")
    assert "mind_app.runtime.turns.root" not in target_modules
    assert "mind_app.interaction.environment" not in target_modules

    violations: list[str] = []
    legacy_module = "mind_app.runtime.mcp.server"
    for path in PROJECT_ROOT.rglob("*.py"):
        source_tree = ast.parse(
            path.read_text(encoding="utf-8-sig"),
            filename=str(path),
        )
        for node in ast.walk(source_tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if legacy_module in modules:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "legacy MCP stdio imports remain:\n" + "\n".join(violations)


def test_mcp_lifecycle_owner_is_harness_owned() -> None:
    """确保 MCP 生命周期所有者只依赖 Harness 端口和注入工厂。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "lifecycle.py"
    owner_path = PROJECT_ROOT / "agent" / "harness" / "mcp" / "owner.py"

    assert not legacy_path.is_file(), "legacy MCP lifecycle owner still exists"
    assert owner_path.is_file(), "Harness MCP lifecycle owner is missing"

    tree = ast.parse(owner_path.read_text(encoding="utf-8-sig"), filename=str(owner_path))
    classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "McpRuntimeOwner" in classes
    assert "McpRuntime" not in classes

    violations: list[str] = []
    legacy_module = "mind_app.runtime.mcp." + "lifecycle"
    for path in PROJECT_ROOT.rglob("*.py"):
        source_tree = ast.parse(
            path.read_text(encoding="utf-8-sig"),
            filename=str(path),
        )
        for node in ast.walk(source_tree):
            imported_module = ""
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                imported_module = node.module or ""
            elif isinstance(node, ast.Import):
                imported_module = next(
                    (
                        alias.name
                        for alias in node.names
                        if alias.name == legacy_module
                    ),
                    "",
                )
            if imported_module == legacy_module:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                )
    assert not violations, "legacy MCP lifecycle imports remain:\n" + "\n".join(
        violations
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.startswith("mind_app") for alias in node.names
        ):
            violations.append(f"{owner_path.relative_to(PROJECT_ROOT)}:{node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module.startswith("mind_app"):
                violations.append(f"{owner_path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "Harness MCP owner crosses legacy boundary:\n" + "\n".join(
        violations
    )

    port_source = (
        PROJECT_ROOT / "agent" / "ports" / "mcp_runtime.py"
    ).read_text(encoding="utf-8-sig")
    runtime_source = (
        PROJECT_ROOT / "infrastructure" / "mcp" / "external_runtime.py"
    ).read_text(encoding="utf-8-sig")
    assert "class McpRuntimeContext" in port_source
    assert "class McpRuntimeHost" not in port_source
    assert "self._host" not in runtime_source
    assert "config_session" not in runtime_source


def test_helix_lifecycle_adapter_is_owned_by_infrastructure() -> None:
    """确保服务生命周期和具体 Helix capability 均由 infrastructure 持有。"""
    adapter_path = PROJECT_ROOT / "infrastructure" / "services" / "helix_capability.py"
    owner_path = PROJECT_ROOT / "infrastructure" / "services" / "runtime_owner.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "service_lifecycle.py"

    assert adapter_path.is_file(), "Helix lifecycle adapter is missing"
    assert owner_path.is_file(), "service runtime owner is missing"
    assert not legacy_path.is_file(), "legacy service runtime owner still exists"
    owner_source = owner_path.read_text(encoding="utf-8-sig")
    assert "class ServiceRuntimeOwner" in owner_source
    assert "mind_app" not in owner_source

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module == "mind_app.runtime.mcp.service_lifecycle":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                    )

    assert not violations, "legacy Helix adapter imports remain:\n" + "\n".join(
        violations
    )


def test_service_runtime_setup_is_infrastructure_owned() -> None:
    """确保服务 setup 归基础设施、前台启动编排归可替换前端。"""
    setup_path = PROJECT_ROOT / "infrastructure" / "services" / "runtime_setup.py"
    environment_path = (
        PROJECT_ROOT
        / "infrastructure"
        / "services"
        / "helix_environment.py"
    )
    runtime_path = PROJECT_ROOT / "frontends" / "helix" / "runtime.py"
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "service_runtime.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "service_exec_env.py",
    )
    assert setup_path.is_file(), "service runtime setup module is missing"
    assert environment_path.is_file(), "Helix environment adapter is missing"
    assert runtime_path.is_file(), "frontend Helix orchestration is missing"
    assert not any(path.is_file() for path in legacy_paths)

    setup_source = setup_path.read_text(encoding="utf-8-sig")
    environment_source = environment_path.read_text(encoding="utf-8-sig")
    runtime_source = runtime_path.read_text(encoding="utf-8-sig")
    assert "mind_app" not in setup_source
    assert "mind_app" not in environment_source
    assert "mind_app" not in runtime_source

    moved_names = {
        "runtime_status",
        "resolve_service_runtime",
        "prepend_runtime_paths",
        "verify_runtime_paths",
        "authorize_runtime_files",
        "service_runtime_asset_missing",
        "ensure_runtime_started",
    }
    runtime_tree = ast.parse(
        runtime_path.read_text(encoding="utf-8-sig"),
        filename=str(runtime_path),
    )
    definitions = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not definitions.intersection(moved_names)

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            if node.module not in {
                "mind_app.runtime.mcp.service_runtime",
                "mind_app.runtime.mcp.service_exec_env",
            }:
                continue
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> "
                f"{node.module}"
            )
    assert not violations, "legacy Helix imports remain:\n" + "\n".join(violations)


def test_external_mcp_infrastructure_has_responsibility_modules() -> None:
    """确保 MCP 配置、SDK 会话和连接生命周期归属基础设施。"""
    target_root = PROJECT_ROOT / "infrastructure" / "mcp"
    assert {
        path.name
        for path in target_root.glob("*.py")
    } == {
        "__init__.py",
        "composite_session.py",
        "errors.py",
        "external_group.py",
        "external_runtime.py",
        "external_status.py",
        "local_session.py",
        "local_tool_factory.py",
        "local_tool_registry.py",
        "nested_tool_results.py",
        "registry.py",
        "settings.py",
        "tool_catalog.py",
        "tool_execution.py",
        "tool_invocation.py",
        "tool_results.py",
        "tool_runtime.py",
        "transport.py",
        "values.py",
    }

    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "config.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "errors.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "external.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "group.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "local.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "registry.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "session_adapter.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "status.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_progress.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_result.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_runtime.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_store.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tools.py",
    )
    assert not any(path.is_file() for path in legacy_paths)

    violations = _forbidden_imports(
        "infrastructure/mcp",
        {"backend", "engine", "frontends", "mind_app", "mind_core", "server"},
    )
    assert not violations, "MCP infrastructure crosses its boundary:\n" + (
        "\n".join(violations)
    )

    legacy_imports = _forbidden_module_imports(
        ".",
        {
            "mind_app.runtime.mcp.config",
            "mind_app.runtime.mcp.errors",
            "mind_app.runtime.mcp.external",
            "mind_app.runtime.mcp.group",
            "mind_app.runtime.mcp.local",
            "mind_app.runtime.mcp.registry",
            "mind_app.runtime.mcp.session_adapter",
            "mind_app.runtime.mcp.status",
            "mind_app.runtime.mcp.tool_progress",
            "mind_app.runtime.mcp.tool_result",
            "mind_app.runtime.mcp.tool_runtime",
            "mind_app.runtime.mcp.tool_store",
            "mind_app.runtime.mcp.tools",
        },
    )
    assert not legacy_imports, "legacy MCP infrastructure imports remain:\n" + (
        "\n".join(legacy_imports)
    )


def test_local_tool_contracts_have_single_ownership_boundary() -> None:
    """确保本地工具契约与 MCP 注册表不在旧能力包中重复实现。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "builtin_tools" / "registry.py",
        PROJECT_ROOT / "mind_app" / "builtin_tools" / "permissions.py",
        PROJECT_ROOT / "mind_app" / "builtin_tools" / "types.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "registry.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "result.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "planning.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "subagents.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "types.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "update_plan.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "view_image.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "coding" / "schemas.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "coding" / "native.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "coding" / "__init__.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "factory.py",
        PROJECT_ROOT / "mind_app" / "client_tools" / "__init__.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "execution_authorization.py",
    )
    assert not any(path.is_file() for path in legacy_paths)
    assert not any(
        (PROJECT_ROOT / "mind_app" / "builtin_tools").glob("*.py")
    )

    legacy_modules = {
        "mind_app.builtin_tools.registry",
        "mind_app.builtin_tools.permissions",
        "mind_app.builtin_tools.types",
        "mind_app.client_tools.registry",
        "mind_app.client_tools.result",
        "mind_app.client_tools.planning",
        "mind_app.client_tools.subagents",
        "mind_app.client_tools.types",
        "mind_app.client_tools.update_plan",
        "mind_app.client_tools.view_image",
        "mind_app.client_tools.coding.schemas",
        "mind_app.client_tools.coding.native",
        "mind_app.client_tools.factory",
        "mind_app.native_coding.execution_authorization",
    }
    violations = _forbidden_module_imports(".", legacy_modules)
    assert not violations, "legacy local tool imports remain:\n" + "\n".join(
        violations
    )

    tool_boundary_violations = _forbidden_imports(
        "agent/application/tools",
        {"agent.harness", "agent.stores", "infrastructure", "mind_app"},
    )
    assert not tool_boundary_violations, (
        "application tools cross their execution boundary:\n"
        + "\n".join(tool_boundary_violations)
    )

    assert not any(
        (PROJECT_ROOT / "mind_app" / "client_tools").glob("**/*.py")
    )

    factory_path = (
        PROJECT_ROOT / "infrastructure" / "mcp" / "local_tool_factory.py"
    )
    factory_tree = ast.parse(
        factory_path.read_text(encoding="utf-8-sig"),
        filename=str(factory_path),
    )
    factory_classes = {
        node.name
        for node in factory_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert not factory_classes, "client tool factory must not own registry state"


def test_tool_runtime_is_composed_at_process_root() -> None:
    """确保组合根注入工具实现且执行资源拥有动态会话状态。"""
    reverse_imports = _forbidden_module_imports(
        "mind_app",
        {"infrastructure.mcp.tool_runtime"},
    )
    assert not reverse_imports, "legacy application constructs tool runtime:\n" + (
        "\n".join(reverse_imports)
    )

    composition = (PROJECT_ROOT / "mind.py").read_text(encoding="utf-8-sig")
    host = (PROJECT_ROOT / "composition.py").read_text(encoding="utf-8-sig")
    resources = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "resources.py"
    ).read_text(encoding="utf-8-sig")
    runtime = (
        PROJECT_ROOT / "infrastructure" / "mcp" / "tool_runtime.py"
    ).read_text(encoding="utf-8-sig")

    assert "def create_tool_runtime(" in composition
    assert "create_tool_runtime=create_tool_runtime" in composition
    assert "create_client_tool_registry=build_client_tool_registry" in composition
    assert "create_builtin_tool_registry=build_builtin_tool_registry" in composition
    assert "ToolRuntimeSources(" in resources
    assert "ToolRuntimeSources(" not in host
    assert "CompositeToolRuntime" not in host
    assert "build_client_tool_registry" not in host
    assert "build_builtin_tool_registry" not in host
    assert "def with_mcp_session(" not in host
    assert "def link_service_mcp(" not in host
    assert "def unlink_service_mcp(" not in host
    assert "def is_service_mcp_linked(" not in host
    assert "self.external_mcp" not in host
    assert "self.client_tools" not in host
    assert "self.builtin_tools" not in host
    assert "self.event_reporting" not in host
    assert "ClientToolProvider" not in runtime
    assert "BuiltinToolProvider" not in runtime
    assert "ExternalMcpProvider" not in runtime
    assert "ServiceMcpProvider" not in runtime


def test_frontend_output_sanitizer_has_no_legacy_source() -> None:
    """确保前端输出脱敏不再由旧应用平铺模块持有。"""
    target_path = PROJECT_ROOT / "frontends" / "output" / "sanitize.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "stream_sanitize.py"
    assert target_path.is_file()
    assert not legacy_path.is_file()

    legacy_imports = _forbidden_module_imports(
        ".",
        {"mind_app.stream_sanitize"},
    )
    assert not legacy_imports, "legacy sanitizer imports remain:\n" + (
        "\n".join(legacy_imports)
    )


def test_process_encoding_has_one_platform_owner() -> None:
    """确保进程输出解码只由平台基础设施实现。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "native_coding" / "encoding.py"
    assert not legacy_path.is_file(), "legacy native coding encoding still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.native_coding.encoding":
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy native coding encoding imports remain:\n" + "\n".join(violations)
    )


def test_workspace_process_boundaries_have_no_legacy_sources() -> None:
    """确保进程树、工作区命令和 Git 差异由平台基础设施持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "processes.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "workspace_command.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "git_diff.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy workspace/process sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.processes",
        "mind_app.native_coding.workspace_command",
        "mind_app.native_coding.git_diff",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy workspace/process imports remain:\n" + "\n".join(violations)
    )


def test_workspace_coding_has_infrastructure_ownership() -> None:
    """确保工作区编码实现按领域与基础设施边界归位。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "native_coding"
    assert not any(legacy_root.rglob("*.py"))

    legacy_imports = _forbidden_module_imports(
        ".",
        {"mind_app.native_coding"},
    )
    assert not legacy_imports, "legacy workspace imports remain:\n" + (
        "\n".join(legacy_imports)
    )

    domain_violations = _forbidden_imports(
        "agent/domain/patches",
        {
            "backend",
            "engine",
            "frontends",
            "infrastructure",
            "metadata",
            "mind_app",
            "mind_core",
            "observability",
            "protocol",
            "server",
        },
    )
    assert not domain_violations, "patch domain crosses boundaries:\n" + (
        "\n".join(domain_violations)
    )

    infrastructure_violations = _forbidden_imports(
        "infrastructure/workspace",
        {"backend", "engine", "frontends", "mind_app", "mind_core", "server"},
    )
    assert not infrastructure_violations, (
        "workspace infrastructure crosses boundaries:\n"
        + "\n".join(infrastructure_violations)
    )

    runtime = (PROJECT_ROOT / "infrastructure" / "workspace" / "runtime.py")
    source = runtime.read_text(encoding="utf-8-sig")
    assert "class WorkspaceCoding(" in source
    assert "class NativeCoding(" not in source

    composition = (PROJECT_ROOT / "mind.py").read_text(encoding="utf-8-sig")
    assert "from infrastructure.workspace.runtime import WorkspaceCoding" in composition
    assert "from mind_app.native_coding" not in composition


def test_command_safety_has_platform_ownership() -> None:
    """确保跨平台危险命令识别不再由 native coding 包持有。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "command_safety"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy command safety sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if (
                    module == "mind_app.native_coding.exec.command_safety"
                    or module.startswith("mind_app.native_coding.exec.command_safety.")
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy command safety imports remain:\n" + "\n".join(violations)
    )


def test_process_execution_substrate_has_platform_ownership() -> None:
    """确保进程捕获、解码、沙箱 sidecar 和 shell 解析由平台基础设施持有。"""
    process_sessions = (
        PROJECT_ROOT / "infrastructure" / "platform" / "process_sessions.py"
    )
    assert process_sessions.is_file(), "platform process session manager is missing"

    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "process_capture.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "process_session.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "output_decoder.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "sandbox_client.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "shell_runtime.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy process execution sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_prefix = "mind_app.native_coding.exec."
    legacy_modules = {
        f"{legacy_prefix}process_capture",
        f"{legacy_prefix}process_session",
        f"{legacy_prefix}output_decoder",
        f"{legacy_prefix}sandbox_client",
        f"{legacy_prefix}shell_runtime",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy process execution imports remain:\n" + "\n".join(violations)
    )

    workspace_runtime_path = (
        PROJECT_ROOT / "infrastructure" / "workspace" / "runtime.py"
    )
    workspace_runtime_tree = ast.parse(
        workspace_runtime_path.read_text(encoding="utf-8-sig"),
        filename=str(workspace_runtime_path),
    )
    workspace_runtime_imports = {
        node.module or ""
        for node in ast.walk(workspace_runtime_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert "infrastructure.platform.sandbox" not in workspace_runtime_imports
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SandboxClient"
        for node in ast.walk(workspace_runtime_tree)
    )

    composition_path = PROJECT_ROOT / "mind.py"
    composition_source = composition_path.read_text(encoding="utf-8-sig")
    assert "SandboxClient(" in composition_source
    assert "ProcessSessionManager(" in composition_source


def test_hook_output_spill_has_platform_ownership() -> None:
    """确保 Hook 大输出的临时文件生命周期由平台基础设施持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "output_spill.py"
    assert not legacy_path.is_file(), "legacy Hook output spill source still exists"

    legacy_modules = {"mind_app.runtime.hooks.output_spill"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy Hook output spill imports remain:\n" + "\n".join(violations)
    )

    platform_path = PROJECT_ROOT / "infrastructure" / "platform" / "hook_output_spill.py"
    tree = ast.parse(
        platform_path.read_text(encoding="utf-8-sig"),
        filename=str(platform_path),
    )
    forbidden = {"agent", "mind_app", "mind_core", "observability"}
    platform_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                platform_violations.append(
                    f"{platform_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not platform_violations, (
        "Hook output spill platform adapter crosses its boundary:\n"
        + "\n".join(platform_violations)
    )


def test_javascript_repl_has_platform_ownership() -> None:
    """确保 JavaScript 内核进程生命周期由平台基础设施持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "native_coding" / "js_repl" / "runtime.py",
        PROJECT_ROOT / "mind_app" / "native_coding" / "js_repl" / "__init__.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy JavaScript REPL sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.native_coding.js_repl",
        "mind_app.native_coding.js_repl.runtime",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy JavaScript REPL imports remain:\n" + "\n".join(violations)
    )


def test_runtime_environment_helpers_have_platform_ownership() -> None:
    """确保 shell 工具路由和工作区探测不再由 runtime 持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "environment" / "shell_tools.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "environment" / "workspace.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy runtime environment sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.environment.shell_tools",
        "mind_app.runtime.environment.workspace",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy runtime environment imports remain:\n" + "\n".join(violations)
    )


def test_workspace_runtime_owner_belongs_to_harness() -> None:
    """确保工作区资源生命周期不再由 legacy runtime 或 Controller 隐式装配。"""
    legacy_path = (
        PROJECT_ROOT
        / "mind_app"
        / "runtime"
        / "environment"
        / "coding_lifecycle.py"
    )
    assert not legacy_path.is_file(), "legacy workspace runtime owner still exists"

    legacy_modules = {
        "mind_app.runtime.environment.coding_lifecycle",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy workspace runtime imports remain:\n" + "\n".join(violations)
    )


def test_environment_snapshot_capture_has_application_ownership() -> None:
    """确保环境快照采集核心脱离 runtime 目录和 Controller 实现。"""
    legacy_path = (
        PROJECT_ROOT
        / "mind_app"
        / "runtime"
        / "environment"
        / "snapshot.py"
    )
    assert not legacy_path.is_file(), "legacy environment snapshot source still exists"

    legacy_modules = {
        "mind_app.runtime.environment.snapshot",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy environment snapshot imports remain:\n" + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "turns" / "environment.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "environment application core crosses its boundary:\n"
        + "\n".join(application_violations)
    )


def test_hook_models_have_application_ownership() -> None:
    """确保 Hook 运行值对象不再由 runtime 目录持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "models.py"
    assert not legacy_path.is_file(), "legacy hook model source still exists"

    legacy_modules = {"mind_app.runtime.hooks.models"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy hook model imports remain:\n" + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "hooks" / "models.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "hook application models cross their boundary:\n"
        + "\n".join(application_violations)
    )


def test_hook_protocol_has_application_ownership() -> None:
    """确保 Hook stdin/stdout schema 不再由 runtime 目录持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "protocol.py"
    assert not legacy_path.is_file(), "legacy hook protocol source still exists"

    legacy_modules = {"mind_app.runtime.hooks.protocol"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy hook protocol imports remain:\n" + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "hooks" / "protocol.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "hook application protocol crosses its boundary:\n"
        + "\n".join(application_violations)
    )


def test_hook_catalog_has_application_ownership() -> None:
    """确保 Hook 管理目录值对象不再由 runtime 目录持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "catalog.py"
    assert not legacy_path.is_file(), "legacy hook catalog source still exists"

    legacy_modules = {"mind_app.runtime.hooks.catalog"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy hook catalog imports remain:\n" + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "hooks" / "catalog.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "hook application catalog crosses its boundary:\n"
        + "\n".join(application_violations)
    )
    assert application_path.is_file(), "application hook catalog source is missing"


def test_hook_matching_belongs_to_domain() -> None:
    """确保 Hook 匹配规则归入 domain 且不反向依赖 application。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "matching.py"
    assert not legacy_path.is_file(), "legacy hook matching source still exists"

    legacy_modules = {"mind_app.runtime.hooks.matching"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy hook matching imports remain:\n" + "\n".join(violations)
    )

    domain_path = PROJECT_ROOT / "agent" / "domain" / "hook_matching.py"
    tree = ast.parse(
        domain_path.read_text(encoding="utf-8-sig"),
        filename=str(domain_path),
    )
    forbidden = {"agent.application", "infrastructure", "mind_app", "mind_core"}
    domain_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module == "agent.application" or module.startswith("agent.application."):
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
            elif module.partition(".")[0] in {"infrastructure", "mind_app", "mind_core"}:
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not domain_violations, (
        "hook domain matching crosses its boundary:\n"
        + "\n".join(domain_violations)
    )
    assert domain_path.is_file(), "domain hook matching source is missing"


def test_hook_output_and_events_have_application_ownership() -> None:
    """确保 Hook 输出归一化和事件目录不再由 runtime 持有。"""
    legacy_modules = {
        "mind_app.runtime.hooks.effects",
        "mind_app.runtime.hooks.events",
    }
    for legacy_name in legacy_modules:
        legacy_path = PROJECT_ROOT / Path(*legacy_name.split("."))
        assert not legacy_path.with_suffix(".py").is_file(), (
            f"legacy Hook module still exists: {legacy_name}"
        )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy Hook output/event imports remain:\n" + "\n".join(violations)
    )

    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    for relative_path in ("hooks/output.py", "hooks/events.py"):
        application_path = PROJECT_ROOT / "agent" / "application" / relative_path
        tree = ast.parse(
            application_path.read_text(encoding="utf-8-sig"),
            filename=str(application_path),
        )
        application_violations: list[str] = []
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in forbidden:
                    application_violations.append(
                        f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

        assert not application_violations, (
            f"Hook application module crosses its boundary: {relative_path}\n"
            + "\n".join(application_violations)
        )


def test_hook_result_projection_has_application_ownership() -> None:
    """确保后置 Hook 工具结果投影不再由 runtime 持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "results.py"
    assert not legacy_path.is_file(), "legacy Hook result projection still exists"

    legacy_modules = {"mind_app.runtime.hooks.results"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy Hook result projection imports remain:\n"
        + "\n".join(violations)
    )

    application_path = PROJECT_ROOT / "agent" / "application" / "hooks" / "result.py"
    tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    forbidden = {"infrastructure", "mind_app", "mind_core", "observability"}
    application_violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden:
                application_violations.append(
                    f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not application_violations, (
        "Hook application result projection crosses its boundary:\n"
        + "\n".join(application_violations)
    )


def test_tool_mode_policy_belongs_to_domain() -> None:
    """确保工具可见性策略归入 domain 且不反向依赖 runtime。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "mode_policy.py"
    assert not legacy_path.is_file(), "legacy tool mode policy source still exists"
    presentation_policy_path = (
        PROJECT_ROOT / "mind_app" / "presentation" / "tool_policy.py"
    )
    assert not presentation_policy_path.is_file(), (
        "legacy presentation tool policy source still exists"
    )

    legacy_modules = {
        "mind_app.runtime.tools.mode_policy",
        "mind_app.presentation.tool_policy",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy tool mode policy imports remain:\n" + "\n".join(violations)
    )

    domain_path = PROJECT_ROOT / "agent" / "domain" / "tool_policy.py"
    tree = ast.parse(
        domain_path.read_text(encoding="utf-8-sig"),
        filename=str(domain_path),
    )
    domain_violations: list[str] = []
    forbidden_roots = {"infrastructure", "mind_app", "mind_core", "observability"}
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module == "agent.application" or module.startswith("agent.application."):
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
            elif module.partition(".")[0] in forbidden_roots:
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not domain_violations, (
        "tool mode policy crosses its domain boundary:\n"
        + "\n".join(domain_violations)
    )


def test_turn_result_and_session_identity_boundaries_are_explicit() -> None:
    """确保运行结果、会话身份和空闲计时器不再由 runtime support 持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "result.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "stream_outcome.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "session_identity.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "idle_status.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "rwlock.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy turn/support sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.turns.result",
        "mind_app.runtime.turns.stream_outcome",
        "mind_app.runtime.support.session_identity",
        "mind_app.runtime.support.idle_status",
        "mind_app.runtime.support.rwlock",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, "legacy turn/support imports remain:\n" + "\n".join(violations)

    for relative_path in (
        "turns/run_result.py",
        "turns/stream_outcome.py",
        "config/session_identity.py",
    ):
        application_path = PROJECT_ROOT / "agent" / "application" / relative_path
        assert application_path.is_file(), (
            f"application source is missing: {relative_path}"
        )
        tree = ast.parse(
            application_path.read_text(encoding="utf-8-sig"),
            filename=str(application_path),
        )
        forbidden = {
            "infrastructure",
            "mind_app",
            "mind_core",
            "observability",
            "protocol",
        }
        application_violations: list[str] = []
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in forbidden:
                    application_violations.append(
                        f"{application_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
        assert not application_violations, (
            f"application turn module crosses its boundary: {relative_path}\n"
            + "\n".join(application_violations)
        )

    old_platform_path = (
        PROJECT_ROOT / "infrastructure" / "platform" / "idle_status.py"
    )
    idle_status_path = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "idle_status.py"
    )
    assert not old_platform_path.is_file(), "platform idle status source remains"
    assert idle_status_path.is_file(), "Harness idle status source is missing"
    idle_status_violations = _forbidden_imports(
        "agent/harness/execution",
        {"backend", "engine", "frontends", "infrastructure", "mind_app", "mind_core", "server"},
    )
    assert not idle_status_violations, (
        "Harness idle status crosses its boundary:\n"
        + "\n".join(idle_status_violations)
    )


def test_runtime_support_responsibilities_have_explicit_owners() -> None:
    """确保会话、TUI 剪贴板和错误展示不再由 runtime support 混合持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "conversation.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "clipboard.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "session_policy.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy runtime support sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    target_paths = (
        PROJECT_ROOT / "agent" / "harness" / "sessions" / "conversation.py",
        PROJECT_ROOT / "frontends" / "tui" / "adapters" / "clipboard.py",
        PROJECT_ROOT / "agent" / "application" / "turns" / "exception_text.py",
    )
    assert all(path.is_file() for path in target_paths), (
        "explicit runtime support owners are missing: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in target_paths
            if not path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.support.conversation",
        "mind_app.runtime.support.clipboard",
        "mind_app.runtime.support.session_policy",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, "legacy runtime support imports remain:\n" + "\n".join(violations)

    mcp_errors = PROJECT_ROOT / "infrastructure" / "mcp" / "errors.py"
    mcp_tree = ast.parse(mcp_errors.read_text(encoding="utf-8-sig"), filename=str(mcp_errors))
    mcp_functions = {
        node.name
        for node in mcp_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "is_transport_close_exception" in mcp_functions

    session_violations = _forbidden_imports(
        "agent/harness/sessions",
        {"engine", "frontends", "infrastructure", "mind_app", "mind_core", "server"},
    )
    assert not session_violations, (
        "Harness session state crosses its boundary:\n"
        + "\n".join(session_violations)
    )

    exception_violations = _forbidden_imports(
        "agent/application/turns",
        {"engine", "frontends", "infrastructure", "mind_app", "mind_core", "server"},
    )
    assert not exception_violations, (
        "application Turn support crosses its boundary:\n"
        + "\n".join(exception_violations)
    )


def test_compaction_result_and_runtime_orchestration_have_separate_owners() -> None:
    """确保压缩结果契约与运行时编排不再混在旧 conversation 模块。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "conversation.py"
    legacy_runtime_path = PROJECT_ROOT / "mind_app" / "runtime" / "compaction.py"
    assert not legacy_path.is_file(), "legacy runtime conversation module still exists"
    assert not legacy_runtime_path.is_file(), "legacy runtime compaction module still exists"

    runtime_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "compaction.py"
    adapter_path = PROJECT_ROOT / "agent" / "adapters" / "protocol" / "compaction.py"
    ports_path = PROJECT_ROOT / "agent" / "ports" / "compaction.py"
    result_path = PROJECT_ROOT / "agent" / "application" / "turns" / "compact_result.py"
    assert runtime_path.is_file(), "Harness compaction orchestration is missing"
    assert adapter_path.is_file(), "protocol compaction adapter is missing"
    assert ports_path.is_file(), "compaction ports are missing"
    assert result_path.is_file(), "application compact result contract is missing"

    legacy_modules = {"mind_app.runtime.conversation"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, "legacy compaction imports remain:\n" + "\n".join(violations)

    result_tree = ast.parse(result_path.read_text(encoding="utf-8-sig"), filename=str(result_path))
    result_classes = {
        node.name
        for node in result_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert result_classes == {"CompactEvent", "CompactResult"}

    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))
    runtime_classes = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "CompactResult" not in runtime_classes
    runtime_source = runtime_path.read_text(encoding="utf-8-sig")
    assert "protocol." not in runtime_source
    assert "mind_app" not in runtime_source
    assert "infrastructure" not in runtime_source

    adapter_source = adapter_path.read_text(encoding="utf-8-sig")
    assert "mind_app" not in adapter_source
    assert "infrastructure" not in adapter_source


def test_execution_context_contracts_are_owned_by_agent_application() -> None:
    """确保 Agent、Turn 和工具调用上下文不再由 mind_app runtime 持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "execution.py"
    target_path = PROJECT_ROOT / "agent" / "application" / "turns" / "context.py"
    assert not legacy_path.is_file(), "legacy runtime execution module still exists"
    assert target_path.is_file(), "application execution contract is missing"

    legacy_modules = {"mind_app.runtime.execution"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, "legacy execution imports remain:\n" + "\n".join(violations)

    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert classes == {"AgentContext", "TurnContext", "ToolInvocation"}
    violations = []
    forbidden_roots = {"infrastructure", "mind_app", "mind_core", "observability", "server"}
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden_roots:
                violations.append(f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}")
    assert not violations, "application execution crosses its boundary:\n" + "\n".join(violations)


def test_agent_mailbox_is_owned_by_stores() -> None:
    """确保子 Agent mailbox 的事件、快照和消费游标归入 stores。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "mailbox.py"
    target_path = PROJECT_ROOT / "agent" / "stores" / "agents" / "mailbox.py"
    assert not legacy_path.is_file(), "legacy runtime mailbox module still exists"
    assert target_path.is_file(), "agent mailbox store is missing"

    legacy_modules = {"mind_app.runtime.subagents.mailbox"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, "legacy mailbox imports remain:\n" + "\n".join(violations)

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server"}:
                target_violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not target_violations, "agent mailbox store crosses its boundary:\n" + "\n".join(target_violations)


def test_agent_thread_context_is_owned_by_application() -> None:
    """确保子 Agent 线程上下文不依赖 runtime 或历史存储实现。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "thread.py"
    target_paths = (
        PROJECT_ROOT / "agent" / "application" / "agents" / "thread.py",
        PROJECT_ROOT / "agent" / "application" / "agents" / "fork_context.py",
    )
    assert not legacy_path.is_file(), "legacy runtime thread module still exists"
    assert all(path.is_file() for path in target_paths), "application thread sources are missing"

    legacy_modules = {"mind_app.runtime.subagents.thread"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
    assert not violations, "legacy subagent thread imports remain:\n" + "\n".join(violations)

    builder_path = PROJECT_ROOT / "agent" / "adapters" / "agents" / "fork_context.py"
    assert builder_path.is_file(), "fork context adapter is missing"
    builder_tree = ast.parse(
        builder_path.read_text(encoding="utf-8-sig"),
        filename=str(builder_path),
    )
    builder_definitions = {
        node.name
        for node in builder_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "load_fork_context" in builder_definitions
    assert not builder_definitions.intersection(
        {"ForkContextSnapshot", "normalize_fork_turns"}
    ), "fork context adapter redefines application contracts"

    for target_path in target_paths:
        tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
        target_violations: list[str] = []
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                    target_violations.append(
                        f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
        assert not target_violations, (
            f"application subagent contract crosses its boundary: {target_path.name}\n"
            + "\n".join(target_violations)
        )

    fork_tree = ast.parse(
        (PROJECT_ROOT / "agent" / "application" / "agents" / "fork_context.py").read_text(encoding="utf-8-sig"),
        filename="agent/application/agents/fork_context.py",
    )
    fork_definitions = {
        node.name
        for node in fork_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"ForkContextEntry", "build_fork_context"}.issubset(fork_definitions)

    thread_tree = ast.parse(
        (PROJECT_ROOT / "agent" / "application" / "agents" / "thread.py").read_text(encoding="utf-8-sig"),
        filename="agent/application/agents/thread.py",
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cast"
        for node in ast.walk(thread_tree)
    )


def test_session_identity_validation_is_owned_by_protocol_schema() -> None:
    """确保 cid/sid 格式校验由协议 schema 统一拥有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "history" / "ids.py"
    target_path = PROJECT_ROOT / "protocol" / "schema" / "identifiers.py"
    assert not legacy_path.is_file(), "legacy history identity module still exists"
    assert target_path.is_file(), "protocol identity schema is missing"

    legacy_modules = {"mind_app.history.ids"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
    assert not violations, "legacy history identity imports remain:\n" + "\n".join(violations)

    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert {"valid_session_ids"}.issubset(definitions)
    assert {"CID_RE", "SID_RE"}.issubset(
        node.targets[0].id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and node.targets
        and isinstance(node.targets[0], ast.Name)
    )


def test_transcript_sink_port_is_owned_by_agent_ports() -> None:
    """确保 Transcript 写入端口不再由历史文件实现包拥有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "history" / "contracts.py"
    target_path = PROJECT_ROOT / "agent" / "ports" / "transcript.py"
    assert not legacy_path.is_file(), "legacy history contract module still exists"
    assert target_path.is_file(), "agent transcript port is missing"

    target_source = target_path.read_text(encoding="utf-8-sig")
    assert "class TranscriptSink" in target_source
    assert "class TranscriptLifecyclePort" in target_source
    assert "class TranscriptSessionPort" in target_source
    finalizer_source = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "turn_finalizer.py"
    ).read_text(encoding="utf-8-sig")
    assert "TranscriptLifecyclePort" in finalizer_source
    assert "typing.cast" not in finalizer_source

    tree = ast.parse(target_source, filename=str(target_path))
    forbidden: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        forbidden.extend(
            f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
            for module in modules
            if module.partition(".")[0] in {
                "mind_app",
                "mind_core",
                "engine",
                "infrastructure",
                "server",
            }
        )
    assert not forbidden, "transcript port imports legacy or infrastructure code:\n" + "\n".join(
        forbidden
    )

    legacy_imports: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if any(
                module == "mind_app.history.contracts"
                or module.startswith("mind_app.history.contracts.")
                for module in modules
            ):
                legacy_imports.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                )
    assert not legacy_imports, "legacy Transcript port imports remain:\n" + "\n".join(
        legacy_imports
    )


def test_agent_graph_persistence_is_owned_by_stores() -> None:
    """确保 Agent 图快照和 SQLite 持久化不再由 runtime 持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "graph.py"
    target_path = PROJECT_ROOT / "agent" / "stores" / "agents" / "graph.py"
    domain_path = PROJECT_ROOT / "agent" / "domain" / "agents.py"
    legacy_control_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "control.py"
    control_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "control.py"
    assert not legacy_path.is_file(), "legacy runtime graph module still exists"
    assert not legacy_control_path.is_file(), "legacy runtime agent control module still exists"
    assert target_path.is_file(), "agent graph store is missing"
    assert domain_path.is_file(), "agent domain status module is missing"
    assert control_path.is_file(), "agent harness control module is missing"

    legacy_modules = {"mind_app.runtime.subagents.graph"}
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
    assert not violations, "legacy graph imports remain:\n" + "\n".join(violations)

    store_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    store_violations: list[str] = []
    for node in ast.walk(store_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server"}:
                store_violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not store_violations, "agent graph store crosses legacy boundary:\n" + "\n".join(store_violations)

    control_tree = ast.parse(control_path.read_text(encoding="utf-8-sig"), filename=str(control_path))
    control_classes = {
        node.name
        for node in control_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert not control_classes.intersection({"AgentGraphRecord", "AgentGraphCheckpoint"})

    control_violations: list[str] = []
    for node in ast.walk(control_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                control_violations.append(
                    f"{control_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not control_violations, "agent harness control crosses legacy boundary:\n" + "\n".join(control_violations)

    domain_tree = ast.parse(domain_path.read_text(encoding="utf-8-sig"), filename=str(domain_path))
    domain_violations: list[str] = []
    for node in ast.walk(domain_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if (
                module == "mind_app"
                or module.startswith("mind_app.")
                or module == "agent.application"
                or module.startswith("agent.application.")
                or module == "agent.stores"
                or module.startswith("agent.stores.")
            ):
                domain_violations.append(
                    f"{domain_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not domain_violations, "agent domain status crosses application boundary:\n" + "\n".join(domain_violations)


def test_agent_views_are_owned_by_application() -> None:
    """确保 Agent 只读快照视图不和 Harness 可变状态机混合。"""
    target_path = PROJECT_ROOT / "agent" / "application" / "agents" / "views.py"
    control_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "control.py"
    assert target_path.is_file(), "agent application views are missing"
    assert control_path.is_file(), "agent harness control is missing"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {
        "AgentMailboxWaitResult",
        "AgentSnapshot",
        "AgentWaitResult",
    }
    target_violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                target_violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not target_violations, "agent application views cross their boundary:\n" + "\n".join(target_violations)

    control_tree = ast.parse(control_path.read_text(encoding="utf-8-sig"), filename=str(control_path))
    control_classes = {
        node.name
        for node in control_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert not control_classes.intersection(target_classes)


def test_agent_message_dispatch_is_owned_by_application() -> None:
    """确保消息派发结果值对象不和活动投递状态机混合。"""
    target_path = PROJECT_ROOT / "agent" / "application" / "agents" / "messages.py"
    delivery_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "delivery.py"
    assert target_path.is_file(), "agent application message result is missing"
    assert delivery_path.is_file(), "agent harness delivery is missing"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"AgentMessageEvent", "AgentMessageDispatch"}

    delivery_tree = ast.parse(delivery_path.read_text(encoding="utf-8-sig"), filename=str(delivery_path))
    delivery_classes = {
        node.name
        for node in delivery_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "AgentMessageDispatch" not in delivery_classes

    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "agent message result crosses legacy boundary:\n" + "\n".join(violations)


def test_agent_control_registry_owns_root_lifecycle() -> None:
    """确保根会话 control 注册表属于 Harness，runtime 不再持有生命周期状态。"""
    registry_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "registry.py"
    runtime_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py"
    assert registry_path.is_file(), "agent control registry is missing"
    assert runtime_path.is_file(), "subagent runtime is missing"

    registry_tree = ast.parse(registry_path.read_text(encoding="utf-8-sig"), filename=str(registry_path))
    registry_classes = {
        node.name
        for node in registry_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert registry_classes == {"AgentControlRegistry"}

    registry_violations: list[str] = []
    for node in ast.walk(registry_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                registry_violations.append(
                    f"{registry_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not registry_violations, "agent control registry crosses legacy boundary:\n" + "\n".join(registry_violations)

    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))
    forbidden_attributes = {"_controls", "_lock", "_shutdown"}
    runtime_attributes = {
        node.attr
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.Attribute)
    }
    assert not runtime_attributes.intersection(forbidden_attributes)


def test_subagent_message_delivery_has_port_and_adapter_owners() -> None:
    """确保子 Agent 消息投递的端口、适配和 Harness 状态各自归属。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "delivery.py"
    runtime_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "delivery.py"
    port_path = PROJECT_ROOT / "agent" / "ports" / "agent_messages.py"
    adapter_path = PROJECT_ROOT / "agent" / "adapters" / "agents" / "messages.py"
    assert not legacy_path.is_file(), "legacy runtime active-turn state machine still exists"
    assert runtime_path.is_file(), "harness active-turn state machine is missing"
    assert port_path.is_file(), "agent message delivery port is missing"
    assert adapter_path.is_file(), "agent message protocol adapter is missing"

    forbidden_names = {
        "AgentMessageReceipt",
        "AgentMessageReceiptStatus",
        "AgentMessageDeliveryPort",
        "SteeringMessageDelivery",
    }
    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))
    runtime_classes = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert not runtime_classes.intersection(forbidden_names)
    assert {
        "AgentActiveTurn",
        "AgentDeliveryRegistry",
    }.issubset(runtime_classes)
    assert "AgentMessageDispatch" not in runtime_classes

    old_imports: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            if node.module == "mind_app.runtime.subagents.delivery":
                old_imports.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not old_imports, "legacy message delivery imports remain:\n" + "\n".join(old_imports)

    runtime_violations: list[str] = []
    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))
    for node in ast.walk(runtime_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                runtime_violations.append(
                    f"{runtime_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not runtime_violations, "harness delivery crosses legacy boundary:\n" + "\n".join(runtime_violations)

    adapter_tree = ast.parse(adapter_path.read_text(encoding="utf-8-sig"), filename=str(adapter_path))
    adapter_violations: list[str] = []
    for node in ast.walk(adapter_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server"}:
                adapter_violations.append(
                    f"{adapter_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not adapter_violations, "message adapter crosses legacy boundary:\n" + "\n".join(adapter_violations)


def test_hook_execution_context_is_owned_by_application() -> None:
    """确保 Hook 输入上下文和执行作用域分别归 application 与 Harness。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "scope.py"
    scope_path = PROJECT_ROOT / "agent" / "harness" / "hooks" / "scope.py"
    target_path = PROJECT_ROOT / "agent" / "application" / "hooks" / "context.py"
    assert not legacy_path.is_file(), "legacy runtime hook scope still exists"
    assert scope_path.is_file(), "Harness hook scope is missing"
    assert target_path.is_file(), "application hook context is missing"

    scope_tree = ast.parse(scope_path.read_text(encoding="utf-8-sig"), filename=str(scope_path))
    scope_classes = {
        node.name
        for node in scope_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "HookExecutionContext" not in scope_classes
    assert "HookExecutionScope" in scope_classes

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            if node.module != "mind_app.runtime.hooks.scope":
                continue
            if node.names:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "legacy HookExecutionScope imports remain:\n" + "\n".join(violations)

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"HookExecutionContext"}

    target_violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                target_violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not target_violations, "application hook context crosses its boundary:\n" + "\n".join(target_violations)


def test_turn_execution_contract_is_owned_by_application() -> None:
    """确保 TurnExecution 只由 application 持有，Harness runner 不定义值对象。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "executor.py"
    runner_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "turn_runner.py"
    target_path = PROJECT_ROOT / "agent" / "application" / "turns" / "execution.py"
    ports_path = PROJECT_ROOT / "agent" / "ports" / "hooks.py"
    assert not legacy_path.is_file(), "legacy runtime turn executor still exists"
    assert runner_path.is_file(), "Harness turn runner is missing"
    assert target_path.is_file(), "application turn execution contract is missing"
    assert ports_path.is_file(), "hook execution scope port is missing"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            if node.module != "mind_app.runtime.turns.executor":
                continue
            violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "legacy turn executor imports remain:\n" + "\n".join(violations)

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"TurnExecution"}
    target_violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                target_violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not target_violations, "application TurnExecution crosses its boundary:\n" + "\n".join(target_violations)

    port_tree = ast.parse(ports_path.read_text(encoding="utf-8-sig"), filename=str(ports_path))
    port_classes = {
        node.name
        for node in port_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "HookExecutionScopePort" in port_classes


def test_turn_executor_uses_runtime_port_without_controller_reflection() -> None:
    """确保 Harness runner 只消费 Turn 运行时端口，不反射具体 Controller。"""
    target_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "turn_runner.py"
    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))

    imported_modules: list[str] = []
    imported_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_modules.append(node.module or "")
            imported_names.extend(alias.name for alias in node.names)

    assert "mind_app.controller" not in imported_modules
    assert "TurnExecutionRuntimePort" in imported_names
    imported_roots = {
        module.partition(".")[0]
        for module in imported_modules
    }
    assert not imported_roots & {
        "backend",
        "engine",
        "frontends",
        "infrastructure",
        "mind_app",
        "mind_core",
        "protocol",
        "server",
    }

    reflected = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
    ]
    assert not reflected


def test_root_turn_preparation_uses_session_port() -> None:
    """确保根轮次准备只读取显式会话端口。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "runtime" / "turns"
    target_path = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "root_runner.py"
    )
    assert not any(legacy_root.glob("*.py")), "legacy runtime turns sources remain"
    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.partition(".")[0]
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_roots.add((node.module or "").partition(".")[0])
    assert not imported_roots & {
        "backend",
        "engine",
        "frontends",
        "infrastructure",
        "mind_app",
        "mind_core",
        "server",
    }
    prepare = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "prepare_root_turn"
    )

    assert prepare.args.args[0].arg == "session"
    controller_accesses = [
        node
        for node in ast.walk(prepare)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "controller"
    ]
    assert not controller_accesses


def test_subagent_runtime_does_not_store_controller_owner() -> None:
    """确保 Subagent runtime 的流式 owner 使用显式执行端口。"""
    target_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py"
    assert not legacy_path.exists()
    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))

    stored_controller = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == "_controller"
    ]
    assert not stored_controller

    concrete_execution_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"execute_turn", "stream_turn"}
    ]
    assert not concrete_execution_calls


def test_mcp_session_contract_is_owned_by_agent_ports() -> None:
    """确保 MCP 会话只由 agent ports 定义，runtime 不保留协议契约。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "contracts.py"
    target_path = PROJECT_ROOT / "agent" / "ports" / "mcp_session.py"
    assert not legacy_path.is_file(), "legacy MCP session contract still exists"
    assert target_path.is_file(), "agent MCP session port is missing"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if "mind_app.runtime.mcp.contracts" in modules:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "legacy MCP session imports remain:\n" + "\n".join(violations)

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"McpSessionPort"}
    target_violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                target_violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not target_violations, "MCP session port crosses its boundary:\n" + "\n".join(target_violations)


def test_turn_and_subagent_execution_ports_are_owned_by_agent_ports() -> None:
    """确保 Turn/Subagent 调用协议不由具体 runtime executor 定义。"""
    targets = {
            PROJECT_ROOT / "agent" / "ports" / "turns.py": {
                "EventReportPort",
                "TurnCleanupPort",
                "TurnEventReportHandle",
                "TurnEventReportingPort",
                "TurnExecutionRuntimePort",
                "TurnStartResultPort",
                "RootTurnSessionPort",
                "TurnInputEventHandler",
                "TurnOperation",
                "RetryStatePort",
                "TurnAnimationPort",
                "TurnSessionContextPort",
                "TurnSessionStatePort",
                "TurnResultPort",
        },
        PROJECT_ROOT / "agent" / "ports" / "subagents.py": {
            "SubagentExecutionPort",
            "SubagentStreamPort",
            "SubagentOperation",
            "SubagentTurnRunner",
            "SubagentCleanupPort",
            "SubagentControlPort",
            "SubagentRuntimeHostPort",
        },
    }
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "executor.py"
    implementation_paths = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "turn_runner.py",
        PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py",
    )
    assert not legacy_path.is_file(), "legacy runtime executor still exists"

    for target_path, expected_classes in targets.items():
        assert target_path.is_file(), f"execution port is missing: {target_path}"
        tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
        classes = {
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        assert classes == expected_classes
        violations: list[str] = []
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                    violations.append(
                        f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
        assert not violations, "execution port crosses its boundary:\n" + "\n".join(violations)

    legacy_violations: list[str] = []
    for path in implementation_paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in {
                "TurnResult",
                "TurnOperation",
                "SubagentExecutionPort",
                "SubagentOperation",
            }:
                legacy_violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {node.name}")
    assert not legacy_violations, "runtime execution contracts remain:\n" + "\n".join(legacy_violations)


def test_subagent_hook_events_are_owned_by_application() -> None:
    """确保子 Agent Hook 生命周期聚合不依赖 runtime scope 实现。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "subagent.py"
    target_path = PROJECT_ROOT / "agent" / "application" / "hooks" / "subagent.py"
    assert not legacy_path.is_file(), "legacy subagent hook module still exists"
    assert target_path.is_file(), "application subagent hook module is missing"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"SubagentHookEvents"}
    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "application subagent hooks cross their boundary:\n" + "\n".join(violations)

    import_violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if "mind_app.runtime.hooks.subagent" in modules:
                import_violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not import_violations, "legacy subagent hook imports remain:\n" + "\n".join(import_violations)


def test_subagent_runner_is_owned_by_harness_without_package_cycle() -> None:
    """确保 SubagentRunner 由 Harness 持有且包初始化不预加载组件。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runner.py"
    target_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "subagent_runner.py"
    package_path = PROJECT_ROOT / "agent" / "harness" / "__init__.py"
    assert not legacy_path.is_file(), "legacy subagent runner still exists"
    assert target_path.is_file(), "harness subagent runner is missing"
    assert package_path.is_file(), "harness package initializer is missing"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"SubagentRunner"}
    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "harness subagent runner crosses legacy boundary:\n" + "\n".join(violations)

    package_tree = ast.parse(package_path.read_text(encoding="utf-8-sig"), filename=str(package_path))
    package_imports = [
        node
        for node in package_tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert not package_imports, "harness package initializer must not preload components"


def test_subagent_submission_execution_is_owned_by_harness() -> None:
    """确保已分配提交的执行协调不回流到 mind_app runtime。"""
    target_path = PROJECT_ROOT / "agent" / "harness" / "execution" / "subagent_submission.py"
    runtime_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py"
    assert target_path.is_file(), "harness subagent submission executor is missing"
    assert runtime_path.is_file(), "subagent runtime is missing"
    assert not legacy_path.exists(), "legacy subagent runtime remains"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"SubagentSubmissionExecutor", "SubagentTurnFailedError"}

    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "harness subagent submission crosses legacy boundary:\n" + "\n".join(violations)

    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))
    runtime_definitions = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "SubagentTurnFailedError" not in runtime_definitions
    assert "_execute_submission" not in runtime_definitions


def test_subagent_runtime_only_orchestrates_injected_ports() -> None:
    """确保 SubagentRuntime 不重新拥有流式和 Turn 执行实现。"""
    runtime_path = PROJECT_ROOT / "agent" / "harness" / "agents" / "runtime.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py"
    assert not legacy_path.exists(), "legacy SubagentRuntime path remains"
    tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_modules.add(node.module or "")

    forbidden = {
        "mind_app.runtime.turns",
        "frontends.output",
        "agent.adapters.agents.execution",
    }
    assert not any(
        module == target or module.startswith(f"{target}.")
        for module in imported_modules
        for target in forbidden
    ), "SubagentRuntime owns legacy execution dependencies"

    runtime_definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert runtime_definitions == {"SubagentRuntime", "_normalize_task"}
    runtime_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SubagentRuntime"
    )
    init_method = next(
        node
        for node in runtime_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "__init__"
    )
    init_arguments = {
        argument.arg
        for argument in (*init_method.args.args, *init_method.args.kwonlyargs)
    }
    assert not init_arguments.intersection({"executor", "turn_runner"})


def test_subagent_stream_execution_is_owned_by_adapter() -> None:
    """确保具体流式 Subagent 执行器由 adapter 持有且不反向加载旧应用。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "executor.py"
    target_path = PROJECT_ROOT / "agent" / "adapters" / "agents" / "execution.py"
    assert not legacy_path.is_file(), "legacy subagent executor still exists"
    assert target_path.is_file(), "subagent execution adapter is missing"

    target_tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert target_classes == {"StreamSubagentExecution"}
    violations: list[str] = []
    for node in ast.walk(target_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                violations.append(
                    f"{target_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )
    assert not violations, "subagent adapter crosses legacy boundary:\n" + "\n".join(violations)

    import_violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if "mind_app.runtime.subagents.executor" in modules:
                import_violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not import_violations, "legacy subagent executor imports remain:\n" + "\n".join(import_violations)


def test_subagent_turn_adapter_receives_output_factory() -> None:
    """确保子 Agent Turn 适配器不自行选择具体输出实现。"""
    legacy_path = (
        PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "subagent_adapter.py"
    )
    target_path = (
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "subagent_stream.py"
    )
    assert not legacy_path.is_file(), "legacy subagent turn adapter remains"
    tree = ast.parse(target_path.read_text(encoding="utf-8-sig"), filename=str(target_path))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_modules.add(node.module or "")

    assert "frontends.output.silent" not in imported_modules
    execution_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ProtocolSubagentStream"
    )
    init_method = next(
        node
        for node in execution_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "__init__"
    )
    init_arguments = {
        argument.arg
        for argument in (*init_method.args.args, *init_method.args.kwonlyargs)
    }
    assert "session_factory" in init_arguments


def test_tool_progress_policy_and_dispatch_have_single_owners() -> None:
    """确保进度策略归 domain，投递归工具执行编排，旧支持模块全部删除。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "notify.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "policy.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_progress.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy tool support sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.runtime.tools.notify",
        "mind_app.runtime.tools.policy",
        "mind_app.runtime.mcp.tool_progress",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, "legacy tool support imports remain:\n" + "\n".join(violations)
    policy = (
        PROJECT_ROOT / "agent" / "domain" / "tool_policy.py"
    ).read_text(encoding="utf-8-sig")
    projection = (
        PROJECT_ROOT / "agent" / "application" / "views" / "tool_execution.py"
    ).read_text(encoding="utf-8-sig")
    assert "def supports_progress_notifications(" in policy
    assert "async def show_tool_progress(" in projection


def test_hook_execution_ports_are_owned_by_agent_ports() -> None:
    """确保 Hook 执行端口不由具体 runtime 模块定义或携带实现依赖。"""
    ports_path = PROJECT_ROOT / "agent" / "ports" / "hooks.py"
    assert ports_path.is_file(), "Hook execution ports source is missing"

    tree = ast.parse(
        ports_path.read_text(encoding="utf-8-sig"),
        filename=str(ports_path),
    )
    violations: list[str] = []
    for node in ast.walk(tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in {
                "infrastructure",
                "mind_app",
                "mind_core",
                "observability",
                "protocol",
            }:
                violations.append(
                    f"{ports_path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                )

    assert not violations, "Hook ports cross their boundary:\n" + "\n".join(violations)


def test_hook_command_executor_is_owned_by_platform_infrastructure() -> None:
    """确保 Hook 子进程执行器不再由 runtime hooks 持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "command.py"
    target_path = PROJECT_ROOT / "infrastructure" / "platform" / "hook_command.py"

    assert not legacy_path.is_file(), "legacy runtime HookCommandExecutor still exists"
    assert target_path.is_file(), "platform HookCommandExecutor is missing"

    target_tree = ast.parse(
        target_path.read_text(encoding="utf-8-sig"),
        filename=str(target_path),
    )
    target_classes = {
        node.name
        for node in target_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert {"HookCommandError", "HookCommandOutput", "HookCommandExecutor"}.issubset(
        target_classes
    )

    legacy_imports: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if "mind_app.runtime.hooks.command" in modules:
                legacy_imports.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not legacy_imports, "legacy HookCommandExecutor imports remain:\n" + "\n".join(
        legacy_imports
    )

    runtime_path = PROJECT_ROOT / "agent" / "harness" / "hooks" / "runtime.py"
    runtime_tree = ast.parse(
        runtime_path.read_text(encoding="utf-8-sig"),
        filename=str(runtime_path),
    )
    local_protocols = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"HookCommandRunner", "HookContextSpiller"}
    }
    assert not local_protocols, (
        "Hook execution ports remain defined in runtime: "
        + ", ".join(sorted(local_protocols))
    )

    for relative_path in (
        "agent/harness/hooks/runtime.py",
        "agent/harness/hooks/registry.py",
    ):
        source_path = PROJECT_ROOT / relative_path
        source_tree = ast.parse(
            source_path.read_text(encoding="utf-8-sig"),
            filename=str(source_path),
        )
        reflected_executor_checks = [
            node
            for node in ast.walk(source_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id == "HookCommandExecutor"
        ]
        assert not reflected_executor_checks, (
            "Hook lifecycle must use explicit ports: "
            + relative_path
        )


def test_hook_runtime_and_registry_are_harness_owned() -> None:
    """确保 Hook 执行状态和 registry 生命周期归 Harness 且不反向依赖平台。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "runtime.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "registry.py",
    )
    target_paths = (
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "runtime.py",
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "registry.py",
    )

    assert not any(path.is_file() for path in legacy_paths), (
        "legacy Hook runtime sources still exist: "
        + ", ".join(str(path.relative_to(PROJECT_ROOT)) for path in legacy_paths if path.is_file())
    )
    assert all(path.is_file() for path in target_paths), "Harness Hook sources are missing"

    violations: list[str] = []
    for path in target_paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.partition(".")[0] in {
                    "engine",
                    "infrastructure",
                    "mind_app",
                    "mind_core",
                    "mind_nova",
                    "server",
                }:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}")
    assert not violations, "Harness Hook implementation crosses boundaries:\n" + "\n".join(
        violations
    )


def test_execution_policy_is_split_between_domain_and_config() -> None:
    """确保执行策略值对象与规则文件解析分别归属 domain/config。"""
    legacy_manager = (
        PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "exec_policy.py"
    )
    manager_path = (
        PROJECT_ROOT / "infrastructure" / "config" / "execution_policy_manager.py"
    )
    assert not legacy_manager.is_file(), "legacy execution policy manager still exists"
    assert manager_path.is_file(), "infrastructure execution policy manager is missing"
    requirement_path = (
        PROJECT_ROOT
        / "agent"
        / "domain"
        / "execution_policy"
        / "requirements.py"
    )
    assert requirement_path.is_file(), "execution policy requirement domain is missing"

    legacy_root = PROJECT_ROOT / "mind_app" / "native_coding" / "exec" / "execpolicy"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy execution policy sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    legacy_modules = {
        "mind_app.native_coding.exec.exec_policy",
        "mind_app.native_coding.exec.execpolicy",
        "mind_app.native_coding.exec.execpolicy.decision",
        "mind_app.native_coding.exec.execpolicy.rule",
        "mind_app.native_coding.exec.execpolicy.policy",
        "mind_app.native_coding.exec.execpolicy.parser",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, (
        "legacy execution policy imports remain:\n" + "\n".join(violations)
    )

    domain_root = PROJECT_ROOT / "agent" / "domain" / "execution_policy"
    domain_violations: list[str] = []
    for path in domain_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module.startswith(("mind_app", "infrastructure")):
                    domain_violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not domain_violations, (
        "execution policy domain imports infrastructure/application code:\n"
        + "\n".join(domain_violations)
    )

    manager_source = manager_path.read_text(encoding="utf-8-sig")
    requirement_source = requirement_path.read_text(encoding="utf-8-sig")
    assert "class ExecApprovalRequirement" not in manager_source
    assert "class ExecPolicyAmendment" not in manager_source
    assert "class ExecutionPolicyRequirement" in requirement_source
    assert "class ExecutionPolicyAmendment" in requirement_source


def test_terminal_presentation_has_no_legacy_design_sources_or_imports() -> None:
    """确保终端展示能力只由 presentation/terminal 持有。"""
    legacy_design_root = PROJECT_ROOT / "mind_core" / "design"
    legacy_paths = (
        *legacy_design_root.rglob("*.py"),
        PROJECT_ROOT / "mind_app" / "runtime" / "design.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy terminal design sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_core.design",
        "mind_app.runtime.design",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if any(
                    module == legacy or module.startswith(f"{legacy}.")
                    for legacy in legacy_modules
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy terminal design imports remain:\n" + "\n".join(violations)


def test_frontend_contracts_have_no_legacy_package_or_imports() -> None:
    """确保通用前端契约和 sink 已归入 presentation 边界。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "frontend"
    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy frontend sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.frontend" or module.startswith("mind_app.frontend."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy frontend imports remain:\n" + "\n".join(violations)


def test_interaction_state_has_responsibility_owned_modules() -> None:
    """确保交互状态按 Session、前端输入和环境适配职责拆分。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "interaction"
    assert not tuple(legacy_root.rglob("*.py")), (
        "legacy interaction sources remain"
    )

    target_paths = (
        PROJECT_ROOT / "agent" / "harness" / "sessions" / "conversation.py",
        PROJECT_ROOT / "frontends" / "interaction" / "attachments.py",
        PROJECT_ROOT / "frontends" / "interaction" / "contracts.py",
        PROJECT_ROOT / "frontends" / "interaction" / "noninteractive.py",
        PROJECT_ROOT / "infrastructure" / "services" / "turn_environment.py",
    )
    assert all(path.is_file() for path in target_paths)

    legacy_imports = _forbidden_module_imports(
        ".",
        {"mind_app.interaction"},
    )
    assert not legacy_imports, "legacy interaction imports remain:\n" + (
        "\n".join(legacy_imports)
    )

    frontend_violations = _forbidden_imports(
        "frontends/interaction",
        {"backend", "engine", "mind_app", "mind_core", "server"},
    )
    assert not frontend_violations, (
        "frontend interaction crosses its boundary:\n"
        + "\n".join(frontend_violations)
    )

    environment_violations = _forbidden_module_imports(
        "infrastructure/services",
        {"mind_app", "mind_core", "engine"},
    )
    assert not environment_violations, (
        "turn environment adapter imports legacy code:\n"
        + "\n".join(environment_violations)
    )


def test_tui_turn_loop_consumes_injected_root_turn_use_case() -> None:
    """确保可替换 TUI 只消费组合根绑定的根轮次用例。"""
    loop_path = PROJECT_ROOT / "frontends" / "tui" / "session" / "loop.py"
    tree = ast.parse(
        loop_path.read_text(encoding="utf-8-sig"),
        filename=str(loop_path),
    )
    loop_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "run_tui_loop"
    )
    keyword_names = {
        argument.arg
        for argument in loop_function.args.kwonlyargs
    }
    assert "turn_runner" in keyword_names
    assert "execution_runtime" not in keyword_names
    assert "root_session" not in keyword_names

    forbidden_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "mind"
        and node.attr in {"turn_execution_runtime", "root_turn_session"}
    }
    assert not forbidden_attributes


def test_application_presentation_ports_are_owned_by_agent() -> None:
    """确保跨入口展示端口不再由 mind_app presentation 持有。"""
    target_path = PROJECT_ROOT / "agent" / "ports" / "presentation.py"
    legacy_path = PROJECT_ROOT / "mind_app" / "presentation" / "application.py"
    assert target_path.is_file(), "application presentation port is missing"
    assert not legacy_path.is_file(), "legacy application module remains"

    target_tree = ast.parse(
        target_path.read_text(encoding="utf-8-sig"),
        filename=str(target_path),
    )
    target_definitions = {
        node.name
        for node in target_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert target_definitions == {
        "ApplicationView",
        "Viewport",
        "ApplicationSink",
        "TurnForegroundLifecyclePort",
        "TextStyle",
        "TextSpan",
        "StyledBlock",
    }

    frontend_port_path = PROJECT_ROOT / "agent" / "ports" / "frontend.py"
    frontend_port_tree = ast.parse(
        frontend_port_path.read_text(encoding="utf-8-sig"),
        filename=str(frontend_port_path),
    )
    frontend_port_definitions = {
        node.name
        for node in frontend_port_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert frontend_port_definitions == {
        "ActivityRuntimePort",
        "AttachmentStatePort",
        "FrontendActivityPort",
        "FrontendPort",
        "TurnCompletionPresenterPort",
    }

    model_path = PROJECT_ROOT / "mind_app" / "presentation" / "models.py"
    assert not model_path.exists(), "legacy presentation models module still exists"

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            if node.module == "mind_app.presentation.application":
                moved_names = {"ApplicationView", "Viewport", "ApplicationSink"}
            elif node.module == "mind_app.presentation.models":
                moved_names = set(alias.name for alias in node.names)
            elif node.module == "mind_app.presentation.contracts":
                moved_names = set(alias.name for alias in node.names)
            else:
                continue
            for alias in node.names:
                if alias.name in moved_names:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {alias.name}"
                    )
    assert not violations, "legacy shared presentation imports remain:\n" + (
        "\n".join(violations)
    )

    views_root = PROJECT_ROOT / "agent" / "application" / "views"
    assert {
        path.name
        for path in views_root.glob("*.py")
    } == {
        "__init__.py",
        "approval.py",
        "commands.py",
        "contracts.py",
        "hooks.py",
        "patch.py",
        "plan.py",
        "progress.py",
        "run.py",
        "tool_display.py",
        "tool_execution.py",
        "tools.py",
    }


def test_frontend_runtime_and_terminal_are_owned_by_frontends() -> None:
    """确保前端组合、应用 sink 和终端实现不再由 mind_app 持有。"""
    target_paths = (
        PROJECT_ROOT / "frontends" / "runtime.py",
        PROJECT_ROOT / "frontends" / "output" / "application.py",
        PROJECT_ROOT / "frontends" / "terminal" / "capabilities.py",
        PROJECT_ROOT / "frontends" / "terminal" / "highlighting.py",
        PROJECT_ROOT / "frontends" / "terminal" / "renderers" / "dispatch.py",
        PROJECT_ROOT / "frontends" / "terminal" / "traces" / "models.py",
    )
    assert all(path.is_file() for path in target_paths)
    assert not (
        PROJECT_ROOT / "frontends" / "terminal" / "turn_lifecycle.py"
    ).exists()
    assert not (
        PROJECT_ROOT / "frontends" / "terminal" / "animation.py"
    ).exists()

    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "presentation" / "application.py",
        PROJECT_ROOT / "mind_app" / "presentation" / "application_sinks.py",
    )
    legacy_presentation = PROJECT_ROOT / "mind_app" / "presentation"
    assert not any(path.is_file() for path in legacy_paths)
    assert not tuple(legacy_presentation.rglob("*.py")), (
        "legacy presentation sources remain"
    )

    violations: list[str] = []
    legacy_modules = ("mind_app.presentation",)
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "")
            for module in modules:
                if any(
                    module == legacy or module.startswith(f"{legacy}.")
                    for legacy in legacy_modules
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
    assert not violations, "legacy frontend presentation imports remain:\n" + (
        "\n".join(violations)
    )


def test_application_view_builders_are_pure_and_owned_by_application() -> None:
    """确保 view builder 只负责构造 application view，不携带渲染实现。"""
    builders_root = PROJECT_ROOT / "agent" / "application" / "views" / "builders"
    legacy_names = (
        "approval_views.py",
        "batch_views.py",
        "lifecycle_views.py",
        "patch_views.py",
        "plan_views.py",
        "progress_views.py",
        "run_views.py",
    )
    assert not any(
        (PROJECT_ROOT / "mind_app" / "presentation" / name).is_file()
        for name in legacy_names
    ), "legacy application view builders remain"

    violations: list[str] = []
    for path in builders_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "")
            for module in modules:
                if module.partition(".")[0] in {
                    "mind_app",
                    "mind_core",
                    "engine",
                    "server",
                    "infrastructure",
                }:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
    assert not violations, "application view builders cross legacy boundary:\n" + (
        "\n".join(violations)
    )


def test_tool_execution_projection_has_application_ownership() -> None:
    """确保工具展示投影归 application，SDK 执行与增强归基础设施。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "display.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "progress.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "enhance_reporter.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "run.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "router.py",
    )
    assert not any(path.exists() for path in legacy_paths)
    legacy_enhancement = (
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "enhancement"
    )
    assert not any(legacy_enhancement.rglob("*.py"))

    application_path = (
        PROJECT_ROOT / "agent" / "application" / "views" / "tool_execution.py"
    )
    application_tree = ast.parse(
        application_path.read_text(encoding="utf-8-sig"),
        filename=str(application_path),
    )
    application_violations: list[str] = []
    forbidden_roots = {
        "backend",
        "engine",
        "frontends",
        "infrastructure",
        "mcp",
        "mind_app",
        "mind_core",
        "protocol",
        "server",
    }
    for node in ast.walk(application_tree):
        modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module or "",)
        for module in modules:
            if module.partition(".")[0] in forbidden_roots:
                application_violations.append(f"{node.lineno} -> {module}")
    assert not application_violations, (
        "tool execution projection crosses application boundary:\n"
        + "\n".join(application_violations)
    )

    infrastructure_paths = (
        PROJECT_ROOT / "infrastructure" / "mcp" / "tool_execution.py",
        PROJECT_ROOT / "infrastructure" / "mcp" / "tool_invocation.py",
        PROJECT_ROOT
        / "infrastructure"
        / "services"
        / "tool_result_enhancement.py",
    )
    assert all(path.is_file() for path in infrastructure_paths)
    infrastructure_violations = _forbidden_module_imports(
        "infrastructure",
        {
            "mind_app.runtime.tools.display",
            "mind_app.runtime.tools.enhance_reporter",
            "mind_app.runtime.tools.enhancement",
            "mind_app.runtime.tools.progress",
            "mind_app.runtime.tools.router",
            "mind_app.runtime.tools.run",
        },
    )
    assert not infrastructure_violations, (
        "tool infrastructure imports retired runtime modules:\n"
        + "\n".join(infrastructure_violations)
    )


def test_tool_execution_orchestration_is_harness_owned() -> None:
    """确保工具、计划和 Hook 生命周期通过稳定适配器归入 Harness。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "runtime" / "tools"
    legacy_hook = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "tool.py"
    assert not any(legacy_root.rglob("*.py"))
    assert not legacy_hook.exists()

    target_paths = (
        PROJECT_ROOT / "agent" / "application" / "tools" / "execution.py",
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "tool_lifecycle.py",
        PROJECT_ROOT / "agent" / "harness" / "tools" / "client_calls.py",
        PROJECT_ROOT / "agent" / "harness" / "tools" / "plan_calls.py",
        PROJECT_ROOT / "agent" / "harness" / "tools" / "plan_execution.py",
    )
    assert all(path.is_file() for path in target_paths)

    violations = _forbidden_imports(
        "agent/harness/tools",
        {
            "backend",
            "engine",
            "frontends",
            "infrastructure",
            "mcp",
            "mind_app",
            "mind_core",
            "protocol",
            "server",
        },
    )
    assert not violations, "Harness tool orchestration crosses adapters:\n" + (
        "\n".join(violations)
    )

    client_source = target_paths[2].read_text(encoding="utf-8-sig")
    assert "CallToolResult" not in client_source
    assert "ToolExecutionAdapter" in client_source

    service_source = (
        PROJECT_ROOT / "agent" / "application" / "services.py"
    ).read_text(encoding="utf-8-sig")
    root_source = (PROJECT_ROOT / "mind.py").read_text(encoding="utf-8-sig")
    assert "tool_execution: ToolExecutionAdapter" in service_source
    assert "tool_execution=McpToolExecutionAdapter()" in root_source


def test_hook_lifecycle_orchestration_is_harness_owned() -> None:
    """确保 Hook 领域生命周期归 Harness，纯展示映射归 application。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "runtime" / "hooks"
    assert not any(legacy_root.rglob("*.py"))

    harness_paths = (
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "compaction.py",
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "presentation.py",
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "session_lifecycle.py",
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "turn_lifecycle.py",
        PROJECT_ROOT / "agent" / "harness" / "hooks" / "tool_lifecycle.py",
    )
    assert all(path.is_file() for path in harness_paths)

    violations = _forbidden_imports(
        "agent/harness/hooks",
        {
            "backend",
            "engine",
            "frontends",
            "infrastructure",
            "mcp",
            "mind_app",
            "mind_core",
            "protocol",
            "server",
        },
    )
    assert not violations, "Harness Hook lifecycle crosses adapters:\n" + (
        "\n".join(violations)
    )

    builder_path = (
        PROJECT_ROOT
        / "agent"
        / "application"
        / "views"
        / "builders"
        / "hooks.py"
    )
    assert builder_path.is_file()
    builder_source = builder_path.read_text(encoding="utf-8-sig")
    assert "build_hook_run_view" in builder_source
    assert "class HookPresentationAdapter" not in builder_source


def test_turn_stream_projection_is_owned_by_application() -> None:
    """确保协议事件判定和生命周期投影不再属于终端展示实现。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "presentation" / "stream" / "assistant_boundary.py",
        PROJECT_ROOT / "mind_app" / "presentation" / "stream" / "lifecycle.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy turn stream projection remains"
    )

    target_paths = (
        PROJECT_ROOT / "agent" / "application" / "turns" / "stream_boundaries.py",
        PROJECT_ROOT / "agent" / "application" / "turns" / "lifecycle.py",
    )
    violations: list[str] = []
    for path in target_paths:
        assert path.is_file(), f"turn stream projection is missing: {path.name}"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "")
            for module in modules:
                if module.partition(".")[0] in {
                    "mind_app",
                    "mind_core",
                    "engine",
                    "server",
                    "infrastructure",
                }:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
    assert not violations, "turn stream projection crosses legacy boundary:\n" + (
        "\n".join(violations)
    )


def test_turn_stream_protocol_boundaries_have_single_owners() -> None:
    """确保模型事件、工具结果和终态展示不再混居旧 Turn 运行时。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "runtime" / "turns"
    legacy_names = {
        "stream.py",
        "stream_approval.py",
        "stream_effects.py",
        "stream_model.py",
        "stream_presentation.py",
        "stream_policy.py",
        "stream_setup.py",
        "stream_tools.py",
        "stream_finalize.py",
    }
    assert not any((legacy_root / name).is_file() for name in legacy_names)

    adapter_paths = (
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "model_events.py",
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "approval_events.py",
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "tool_events.py",
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "tool_results.py",
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "turn_setup.py",
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "model_request.py",
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "turn_interrupts.py",
        PROJECT_ROOT / "agent" / "adapters" / "protocol" / "turn_stream.py",
    )
    assert all(path.is_file() for path in adapter_paths)
    turn_stream_path = adapter_paths[-1]
    assert len(turn_stream_path.read_text(encoding="utf-8-sig").splitlines()) <= 800
    adapter_violations = _forbidden_imports(
        "agent/adapters/protocol",
        {
            "backend",
            "engine",
            "frontends",
            "infrastructure",
            "mind_app",
            "mind_core",
            "server",
        },
    )
    assert not adapter_violations, "protocol adapters cross host boundaries:\n" + (
        "\n".join(adapter_violations)
    )

    presentation_path = (
        PROJECT_ROOT / "agent" / "application" / "turns" / "presentation.py"
    )
    assert presentation_path.is_file()
    presentation_source = presentation_path.read_text(encoding="utf-8-sig")
    assert "protocol.transport" not in presentation_source
    assert "protocol.client" not in presentation_source
    assert "infrastructure" not in presentation_source

    local_policy_path = (
        PROJECT_ROOT
        / "agent"
        / "application"
        / "approvals"
        / "local_policy.py"
    )
    local_policy_source = local_policy_path.read_text(encoding="utf-8-sig")
    assert "protocol." not in local_policy_source
    assert "infrastructure" not in local_policy_source
    assert "typing.cast" not in local_policy_source

    transcript_path = (
        PROJECT_ROOT / "agent" / "application" / "turns" / "transcript.py"
    )
    transcript_source = transcript_path.read_text(encoding="utf-8-sig")
    assert "protocol." not in transcript_source
    assert "infrastructure" not in transcript_source

    finalizer_path = (
        PROJECT_ROOT / "agent" / "harness" / "execution" / "turn_finalizer.py"
    )
    finalizer_source = finalizer_path.read_text(encoding="utf-8-sig")
    assert "IdleStatusPort" in finalizer_source
    assert "protocol." not in finalizer_source
    assert "infrastructure" not in finalizer_source
    assert "mind_app" not in finalizer_source

    report_port_consumers = (
        PROJECT_ROOT / "agent" / "ports" / "turns.py",
        PROJECT_ROOT / "agent" / "ports" / "subagents.py",
        PROJECT_ROOT / "agent" / "harness" / "execution" / "subagent_runner.py",
        PROJECT_ROOT
        / "agent"
        / "harness"
        / "execution"
        / "subagent_submission.py",
    )
    for path in report_port_consumers:
        source = path.read_text(encoding="utf-8-sig")
        assert "protocol.transport.events" not in source
        assert "EventReportPort" in source


def test_tui_contracts_are_owned_by_frontends() -> None:
    """确保 TUI 展示契约独立于旧应用运行时。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "tui" / "contracts"
    target_root = PROJECT_ROOT / "frontends" / "tui" / "contracts"
    assert not legacy_root.exists(), "legacy TUI contract package still exists"

    expected_files = {
        "__init__.py",
        "menu.py",
        "pager.py",
        "resume.py",
        "screen.py",
        "text.py",
        "transcript.py",
        "views.py",
    }
    assert {
        path.name
        for path in target_root.glob("*.py")
    } == expected_files

    forbidden_prefixes = (
        "mind_app",
        "agent",
        "infrastructure",
        "engine",
        "server",
    )
    violations: list[str] = []
    for path in target_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "" or module.startswith(forbidden_prefixes):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "TUI contracts import forbidden packages:\n" + "\n".join(
        violations
    )

    old_imports: list[str] = []
    old_tui_root = PROJECT_ROOT / "mind_app" / "tui"
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        relative_to_tui = path.is_relative_to(old_tui_root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
                if any(
                    module == "mind_app.tui.contracts"
                    or module.startswith("mind_app.tui.contracts.")
                    for module in modules
                ):
                    old_imports.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                    )
            elif (
                relative_to_tui
                and isinstance(node, ast.ImportFrom)
                and node.level > 0
                and (node.module or "").startswith("contracts")
            ):
                old_imports.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert not old_imports, "legacy TUI contract imports remain:\n" + "\n".join(
        old_imports
    )


def test_tui_adapter_is_owned_by_frontends() -> None:
    """确保 TUI 整体归属 frontends，避免旧包与前端互相导入。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "tui"
    target_root = PROJECT_ROOT / "frontends" / "tui"
    assert not legacy_root.exists(), "legacy TUI package still exists"
    assert (
        PROJECT_ROOT / "agent" / "application" / "approvals" / "presentation.py"
    ).is_file(), "approval application model is missing"
    assert not (
        PROJECT_ROOT / "mind_app" / "approval" / "presentation.py"
    ).is_file(), "legacy approval presentation remains"
    assert not (
        target_root / "core" / "approval_presentation.py"
    ).is_file(), "approval application model remains in the TUI"
    assert not tuple(
        (PROJECT_ROOT / "mind_app" / "approval").glob("*.py")
    ), "legacy approval application sources remain"

    expected_children = {
        "__init__.py",
        "adapters",
        "application.py",
        "contracts",
        "core",
        "features",
        "prompting",
        "rendering",
        "runtime",
        "session",
    }
    assert {
        path.name
        for path in target_root.iterdir()
        if path.name != "__pycache__"
    } == expected_children

    legacy_imports: list[str] = []
    for path in (PROJECT_ROOT / "mind_app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if any(
                module == "frontends.tui" or module.startswith("frontends.tui.")
                for module in modules
            ):
                legacy_imports.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                )

    assert not legacy_imports, "mind_app imports the TUI frontend:\n" + "\n".join(
        legacy_imports
    )


def test_subscription_adapter_is_owned_by_frontends() -> None:
    """确保 Subscription 适配器和远端 wire client 不再挂在 mind_app runtime。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "subscription",
        PROJECT_ROOT / "mind_app" / "runtime" / "agent",
    )
    legacy_sources = tuple(
        path
        for root in legacy_paths
        for path in (root.rglob("*.py") if root.is_dir() else (root,))
        if path.is_file()
    )
    assert not legacy_sources, (
        "legacy Subscription sources remain: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    frontend_root = PROJECT_ROOT / "frontends" / "subscription"
    expected_files = {
        "__init__.py",
        "client.py",
        "wire.py",
        "external_access.py",
        "forwarding.py",
        "loop.py",
        "models.py",
        "opening.py",
        "runtime.py",
        "status.py",
        "ws.py",
    }
    assert {
        path.name
        for path in frontend_root.glob("*.py")
    } == expected_files

    legacy_imports: list[str] = []
    for path in frontend_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app" or module.startswith("mind_app."):
                    legacy_imports.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
    assert not legacy_imports, (
        "legacy Subscription imports remain:\n" + "\n".join(legacy_imports)
    )

    runtime_source = (frontend_root / "runtime.py").read_text(encoding="utf-8-sig")
    assert "turn_application_factory" in runtime_source
    assert "runtime_services" not in runtime_source, (
        "Subscription frontend must not discover runtime services dynamically"
    )

    tui_input_source = (
        PROJECT_ROOT / "frontends" / "tui" / "session" / "turn_input.py"
    ).read_text(encoding="utf-8-sig")
    assert "protocol_client" in tui_input_source
    assert "runtime_services" not in tui_input_source, (
        "TUI turn input must not discover runtime services dynamically"
    )

    tui_loop_source = (
        PROJECT_ROOT / "frontends" / "tui" / "session" / "loop.py"
    ).read_text(encoding="utf-8-sig")
    assert "turn_application_factory" in tui_loop_source
    assert "turn_runner" in tui_loop_source
    assert "execution_runtime" not in tui_loop_source
    assert "root_session" not in tui_loop_source
    assert "runtime_services" not in tui_loop_source, (
        "TUI session loop must not discover runtime services dynamically"
    )

    tui_turn_path = (
        PROJECT_ROOT / "frontends" / "tui" / "session" / "turn.py"
    )
    tui_turn_source = tui_turn_path.read_text(encoding="utf-8-sig")
    assert "class TuiRootTurnRunner(typing.Protocol)" in tui_turn_source
    assert "execution_runtime" not in tui_turn_source
    assert "root_session" not in tui_turn_source

    tui_turn_tree = ast.parse(tui_turn_source, filename=str(tui_turn_path))
    tui_turn_legacy_imports = [
        node.module or ""
        for node in ast.walk(tui_turn_tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and (
            (node.module or "") == "mind_app"
            or (node.module or "").startswith("mind_app.")
        )
    ]
    assert not tui_turn_legacy_imports, (
        "TUI turn adapter imports legacy application runtime: "
        f"{tui_turn_legacy_imports}"
    )

    tui_conversation_source = (
        PROJECT_ROOT / "frontends" / "tui" / "features" / "conversation.py"
    ).read_text(encoding="utf-8-sig")
    assert "class ConversationCompactor(typing.Protocol)" in (
        tui_conversation_source
    )
    assert "mind_app.runtime.compaction" not in tui_conversation_source
    assert "protocol_client" in tui_conversation_source
    assert "runtime_services" not in tui_conversation_source, (
        "TUI conversation features must not discover runtime services dynamically"
    )

    cli_dispatch_source = (
        PROJECT_ROOT / "frontends" / "cli" / "dispatch.py"
    ).read_text(encoding="utf-8-sig")
    assert "turn_application_factory" in cli_dispatch_source
    assert "runtime_services" not in cli_dispatch_source, (
        "CLI dispatch must not discover runtime services dynamically"
    )


def test_tui_runtime_exposes_only_explicit_control_and_lifecycle_ports() -> None:
    """确保 TUI 不保留模块命令替身或生命周期内部状态代理。"""
    turn_input_path = (
        PROJECT_ROOT / "frontends" / "tui" / "session" / "turn_input.py"
    )
    turn_input_source = turn_input_path.read_text(encoding="utf-8-sig")
    turn_input_tree = ast.parse(turn_input_source, filename=str(turn_input_path))
    control_class = next(
        node
        for node in turn_input_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "TuiTurnInputControl"
    )
    initializer = next(
        node
        for node in control_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "__init__"
    )
    protocol_index = next(
        index
        for index, argument in enumerate(initializer.args.kwonlyargs)
        if argument.arg == "protocol_client"
    )
    protocol_argument = initializer.args.kwonlyargs[protocol_index]
    assert initializer.args.kw_defaults[protocol_index] is None
    assert protocol_argument.annotation is not None
    assert ast.unparse(protocol_argument.annotation) == "ProtocolCommandClient"

    forbidden_command_seams = {
        "steer_turn",
        "interrupt_turn",
        "reconcile_turn_inputs",
    }
    module_assignments = {
        node.target.id
        for node in turn_input_tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    module_assignments.update(
        target.id
        for node in turn_input_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    )
    assert not forbidden_command_seams.intersection(module_assignments)
    assert "protocol_client is not None" not in turn_input_source

    runtime_source = (
        PROJECT_ROOT / "frontends" / "tui" / "core" / "runtime.py"
    ).read_text(encoding="utf-8-sig")
    for private_proxy in (
        "def _application_task(",
        "def _application_error(",
        "def _application_failure(",
    ):
        assert private_proxy not in runtime_source

    lifecycle_source = (
        PROJECT_ROOT / "frontends" / "tui" / "runtime" / "lifecycle.py"
    ).read_text(encoding="utf-8-sig")
    for raw_state_accessor in (
        "def task(",
        "def error(",
        "def failure(",
    ):
        assert raw_state_accessor not in lifecycle_source


def test_cli_adapter_is_owned_by_frontends() -> None:
    """确保 CLI 命令解析、路由和入口生命周期归入前端边界。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "cli"
    target_root = PROJECT_ROOT / "frontends" / "cli"
    assert not legacy_root.exists(), "legacy CLI package still exists"
    expected_files = {
        "__init__.py",
        "arguments.py",
        "bootstrap.py",
        "commands.py",
        "completion.py",
        "dispatch.py",
        "doctor.py",
        "entry.py",
        "frontend.py",
        "help.py",
        "invocation.py",
        "mcp_parser.py",
        "mcp_registry.py",
        "parser.py",
        "selection.py",
        "session_archive.py",
    }
    assert {
        path.name
        for path in target_root.glob("*.py")
    } == expected_files

    violations: list[str] = []
    reverse_dependencies: list[str] = []
    legacy_module = "mind_app.cli"
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if any(
                module == legacy_module or module.startswith(legacy_module + ".")
                for module in modules
            ):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    for path in target_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            if any(
                module in {
                    "mind_app.runtime.turns.root",
                    "mind_app.interaction.environment",
                }
                for module in modules
            ):
                reverse_dependencies.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
                )
    assert not violations, "legacy CLI imports remain:\n" + "\n".join(violations)
    assert not reverse_dependencies, (
        "CLI imports legacy execution dependencies:\n"
        + "\n".join(reverse_dependencies)
    )


def test_frontends_use_injected_application_hosts() -> None:
    """确保所有前端通过宿主契约组合，不依赖旧应用实现。"""
    frontend_root = PROJECT_ROOT / "frontends"
    violations: list[str] = []
    for path in frontend_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app" or module.startswith("mind_app."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
    assert not violations, (
        "frontends import the legacy application:\n" + "\n".join(violations)
    )

    cli_bootstrap = (
        frontend_root / "cli" / "bootstrap.py"
    ).read_text(encoding="utf-8-sig")
    cli_dispatch = (
        frontend_root / "cli" / "dispatch.py"
    ).read_text(encoding="utf-8-sig")
    mcp_server = (
        frontend_root / "mcp" / "server.py"
    ).read_text(encoding="utf-8-sig")
    composition = (PROJECT_ROOT / "mind.py").read_text(encoding="utf-8-sig")

    assert "class CliApplicationHost(CliCommandHost, typing.Protocol)" in (
        cli_bootstrap
    )
    assert "class CliCommandHost(typing.Protocol)" in cli_dispatch
    assert "class McpApplicationHost(typing.Protocol)" in mcp_server
    assert "application_host_factory=create_application_host" in composition
    reverse_violations = _forbidden_imports("mind_app", {"frontends"})
    assert not reverse_violations, (
        "legacy application imports frontend implementations:\n"
        + "\n".join(reverse_violations)
    )


def test_presentation_output_has_no_legacy_package_or_imports() -> None:
    """确保跨前端输出端口和实现不再由 mind_app presentation 持有。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "output"
    legacy_presentation_root = PROJECT_ROOT / "mind_app" / "presentation" / "output"
    frontend_output_root = PROJECT_ROOT / "frontends" / "output"
    contracts_path = PROJECT_ROOT / "mind_app" / "presentation" / "output" / "contracts.py"
    content_path = PROJECT_ROOT / "agent" / "ports" / "content.py"
    output_path = PROJECT_ROOT / "agent" / "ports" / "output.py"
    legacy_content_path = PROJECT_ROOT / "mind_app" / "presentation" / "output" / "content.py"
    legacy_session_path = PROJECT_ROOT / "mind_app" / "presentation" / "output" / "session.py"
    legacy_presentation_sources = tuple(legacy_presentation_root.glob("*.py"))
    assert not legacy_presentation_sources, "legacy presentation output sources remain"
    assert {
        path.name
        for path in frontend_output_root.glob("*.py")
    } == {
        "__init__.py",
        "application.py",
        "boundary.py",
        "jsonl.py",
        "recording.py",
        "sanitize.py",
        "silent.py",
        "source_text.py",
        "terminal_content.py",
        "text.py",
    }
    assert not contracts_path.exists(), "legacy output contract module remains"
    assert not legacy_content_path.exists(), "legacy content contract module remains"
    assert not legacy_session_path.exists(), "legacy output session module remains"
    assert content_path.is_file(), "content port module is missing"
    assert output_path.is_file(), "output port module is missing"
    content_tree = ast.parse(
        content_path.read_text(encoding="utf-8-sig"),
        filename=str(content_path),
    )
    content_definitions = {
        node.name
        for node in content_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert content_definitions == {
        "ResponseIdentity",
        "AssistantTextDelta",
        "AssistantSegmentCompleted",
        "AssistantOutputBoundary",
        "AssistantPresentationSuperseded",
        "AssistantResponseSuperseded",
        "SourcesOutput",
        "ContentSink",
    }
    output_tree = ast.parse(
        output_path.read_text(encoding="utf-8-sig"),
        filename=str(output_path),
    )
    output_definitions = {
        node.name
        for node in output_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert output_definitions == {
        "IdleStatusPort",
        "OutputControlPort",
        "OutputStatusPort",
        "OutputPort",
        "OutputPresentationPort",
        "OutputSession",
        "OutputSessionFactory",
    }
    port_violations: list[str] = []
    for path, tree in (
        (content_path, content_tree),
        (output_path, output_tree),
    ):
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "")
            for module in modules:
                if module.partition(".")[0] in {"mind_app", "mind_core", "engine", "server", "infrastructure"}:
                    port_violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )
    assert not port_violations, "output ports cross their boundary:\n" + "\n".join(port_violations)

    legacy_sources = tuple(legacy_root.rglob("*.py"))
    assert not legacy_sources, (
        "legacy output sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if module == "mind_app.output" or module.startswith("mind_app.output."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, "legacy output imports remain:\n" + "\n".join(violations)


def test_presentation_stream_has_no_legacy_packages_or_facades() -> None:
    """确保流事件展示、输出记录和边界状态只存在于 presentation。"""
    legacy_roots = (
        PROJECT_ROOT / "mind_app" / "stream_events",
        PROJECT_ROOT / "mind_app" / "stream_io",
        PROJECT_ROOT / "mind_app" / "stream_state",
    )
    legacy_sources = tuple(
        path
        for root in legacy_roots
        for path in root.rglob("*.py")
    )
    assert not legacy_sources, (
        "legacy stream presentation sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_sources
        )
    )

    legacy_modules = (
        "mind_app.stream_events",
        "mind_app.stream_io",
        "mind_app.stream_state",
        "mind_app.presentation.stream.tool_trace",
    )
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = (node.module or "",)
            for module in modules:
                if any(
                    module == legacy or module.startswith(f"{legacy}.")
                    for legacy in legacy_modules
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {module}"
                    )

    assert not violations, (
        "legacy stream presentation imports remain:\n" + "\n".join(violations)
    )


def test_approval_state_stores_have_no_legacy_package_or_imports() -> None:
    """确保审批调用账本和权限授权存储由 agent/stores 持有。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "approval" / "ledger.py",
        PROJECT_ROOT / "mind_app" / "approval" / "permission_grants.py",
    )
    assert not any(path.is_file() for path in legacy_paths), (
        "legacy approval state sources still exist: "
        + ", ".join(
            str(path.relative_to(PROJECT_ROOT))
            for path in legacy_paths
            if path.is_file()
        )
    )

    legacy_modules = {
        "mind_app.approval.ledger",
        "mind_app.approval.permission_grants",
    }
    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    assert not violations, "legacy approval state imports remain:\n" + "\n".join(violations)


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
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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

    for path in PROJECT_ROOT.rglob("*.py"):
        relative_parts = set(path.relative_to(PROJECT_ROOT).parts)
        if relative_parts & excluded_parts:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
