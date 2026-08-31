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


def test_shared_core_does_not_depend_on_application_or_services() -> None:
    violations = _forbidden_imports(
        "mind_core",
        {"backend", "mind_app", "server"},
    )

    assert not violations, "mind_core crosses its package boundary:\n" + "\n".join(
        violations
    )


def test_application_does_not_import_packaged_backend() -> None:
    violations = _forbidden_imports("mind_app", {"backend"})

    assert not violations, "mind_app imports backend:\n" + "\n".join(violations)


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
        "application/turns/commands.py",
        "application/turns/compact_result.py",
        "application/turns/context.py",
        "application/turns/environment.py",
        "application/turns/execution.py",
        "application/turns/projections.py",
        "application/turns/run_result.py",
        "application/turns/stream_outcome.py",
        "harness/agents/control.py",
        "harness/agents/delivery.py",
        "harness/agents/registry.py",
        "harness/execution/actor.py",
        "harness/execution/subagent_runner.py",
        "harness/execution/subagent_submission.py",
        "harness/subscription/owner.py",
        "harness/sessions/loop.py",
        "harness/sessions/owner.py",
        "stores/agents/graph.py",
        "stores/agents/mailbox.py",
        "stores/approvals/ledger.py",
        "stores/approvals/permissions.py",
        "stores/effects/journal.py",
        "stores/runs/records.py",
        "stores/runs/schema.py",
        "stores/runs/store.py",
        "adapters/agents/execution.py",
        "adapters/agents/messages.py",
        "adapters/protocol/client.py",
        "adapters/protocol/items.py",
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
    } == {"__init__.py", "workspace_runtime.py"}
    assert {
        path.name
        for path in (PROJECT_ROOT / "agent" / "stores").glob("*.py")
    } == {"__init__.py"}
    assert {
        path.name
        for path in (PROJECT_ROOT / "agent" / "adapters").glob("*.py")
    } == {"__init__.py"}

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
    legacy_source = legacy_path.read_text(encoding="utf-8-sig")
    assert "class RootTurnCommandExecutor" not in legacy_source

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


def test_foreground_turn_lifecycle_is_owned_by_terminal_presentation() -> None:
    """确保动画和终端进度生命周期不由 runtime root 定义。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "root.py"
    target_path = (
        PROJECT_ROOT
        / "mind_app"
        / "presentation"
        / "terminal"
        / "turn_lifecycle.py"
    )

    assert target_path.is_file(), "terminal turn lifecycle is missing"
    legacy_tree = ast.parse(
        legacy_path.read_text(encoding="utf-8-sig"),
        filename=str(legacy_path),
    )
    legacy_functions = {
        node.name
        for node in legacy_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "run_foreground_turn" not in legacy_functions

    target_tree = ast.parse(
        target_path.read_text(encoding="utf-8-sig"),
        filename=str(target_path),
    )
    target_functions = {
        node.name
        for node in target_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert target_functions == {"run_foreground_turn"}


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
    """确保服务路径、环境和权限 setup 不再由 runtime 模块定义。"""
    setup_path = PROJECT_ROOT / "infrastructure" / "services" / "runtime_setup.py"
    runtime_path = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "service_runtime.py"
    assert setup_path.is_file(), "service runtime setup module is missing"
    assert runtime_path.is_file(), "service runtime orchestration is missing"

    setup_source = setup_path.read_text(encoding="utf-8-sig")
    assert "mind_app" not in setup_source

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
            if node.module != "mind_app.runtime.mcp.service_runtime":
                continue
            imported_names = {
                alias.name
                for alias in node.names
            }
            moved = imported_names.intersection(moved_names)
            if moved:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> "
                    + ", ".join(sorted(moved))
                )
    assert not violations, "legacy setup imports remain:\n" + "\n".join(violations)


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

    native_coding_path = (
        PROJECT_ROOT / "mind_app" / "native_coding" / "native_coding.py"
    )
    native_tree = ast.parse(
        native_coding_path.read_text(encoding="utf-8-sig"),
        filename=str(native_coding_path),
    )
    native_imports = {
        node.module or ""
        for node in ast.walk(native_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert "infrastructure.platform.sandbox" not in native_imports
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SandboxClient"
        for node in ast.walk(native_tree)
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

    legacy_modules = {"mind_app.runtime.tools.mode_policy"}
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

    platform_path = PROJECT_ROOT / "infrastructure" / "platform" / "idle_status.py"
    assert platform_path.is_file(), "platform idle status source is missing"
    platform_violations = _forbidden_imports(
        "infrastructure/platform",
        {"engine", "mind_app", "mind_core", "server"},
    )
    assert not platform_violations, (
        "platform idle status crosses its boundary:\n"
        + "\n".join(platform_violations)
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
        PROJECT_ROOT / "mind_app" / "interaction" / "conversation.py",
        PROJECT_ROOT / "mind_app" / "tui" / "adapters" / "clipboard.py",
        PROJECT_ROOT / "mind_app" / "presentation" / "stream" / "exception_text.py",
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

    mcp_errors = PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "errors.py"
    mcp_tree = ast.parse(mcp_errors.read_text(encoding="utf-8-sig"), filename=str(mcp_errors))
    mcp_functions = {
        node.name
        for node in mcp_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "is_transport_close_exception" in mcp_functions

    presentation_violations = _forbidden_imports(
        "mind_app/presentation/stream",
        {"engine", "mind_core", "server"},
    )
    assert not presentation_violations, (
        "stream exception presentation crosses its boundary:\n"
        + "\n".join(presentation_violations)
    )


def test_compaction_result_and_runtime_orchestration_have_separate_owners() -> None:
    """确保压缩结果契约与运行时编排不再混在旧 conversation 模块。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "conversation.py"
    assert not legacy_path.is_file(), "legacy runtime conversation module still exists"

    runtime_path = PROJECT_ROOT / "mind_app" / "runtime" / "compaction.py"
    result_path = PROJECT_ROOT / "agent" / "application" / "turns" / "compact_result.py"
    assert runtime_path.is_file(), "runtime compaction orchestration is missing"
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
    assert result_classes == {"CompactResult"}

    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"), filename=str(runtime_path))
    runtime_classes = {
        node.name
        for node in runtime_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "CompactResult" not in runtime_classes


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

    builder_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "context.py"
    builder_tree = ast.parse(
        builder_path.read_text(encoding="utf-8-sig"),
        filename=str(builder_path),
    )
    builder_definitions = {
        node.name
        for node in builder_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not builder_definitions.intersection(
        {"ForkContextSnapshot", "normalize_fork_turns"}
    ), "runtime context builder redefines application contracts"

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
    runtime_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py"
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
    """确保 TurnExecution 只由 application 持有，runtime executor 不定义值对象。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "executor.py"
    target_path = PROJECT_ROOT / "agent" / "application" / "turns" / "execution.py"
    ports_path = PROJECT_ROOT / "agent" / "ports" / "hooks.py"
    assert legacy_path.is_file(), "runtime turn executor is missing"
    assert target_path.is_file(), "application turn execution contract is missing"
    assert ports_path.is_file(), "hook execution scope port is missing"

    legacy_tree = ast.parse(legacy_path.read_text(encoding="utf-8-sig"), filename=str(legacy_path))
    legacy_classes = {
        node.name
        for node in legacy_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "TurnExecution" not in legacy_classes

    violations: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            if node.module != "mind_app.runtime.turns.executor":
                continue
            if any(alias.name == "TurnExecution" for alias in node.names):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, "legacy TurnExecution imports remain:\n" + "\n".join(violations)

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
            "TurnInputEventHandler",
            "TurnOperation",
            "TurnResultPort",
        },
        PROJECT_ROOT / "agent" / "ports" / "subagents.py": {
            "SubagentExecutionPort",
            "SubagentStreamPort",
            "SubagentOperation",
            "SubagentTurnRunner",
            "SubagentCleanupPort",
        },
    }
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "turns" / "executor.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py",
    )

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
    for path in legacy_paths:
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
    runtime_path = PROJECT_ROOT / "mind_app" / "runtime" / "subagents" / "runtime.py"
    assert target_path.is_file(), "harness subagent submission executor is missing"
    assert runtime_path.is_file(), "subagent runtime is missing"

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


def test_tool_progress_has_mcp_ownership_and_dead_policy_is_removed() -> None:
    """确保 MCP 进度通知归入 MCP runtime 且无调用者的策略模块已删除。"""
    legacy_paths = (
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "notify.py",
        PROJECT_ROOT / "mind_app" / "runtime" / "tools" / "policy.py",
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
    assert (
        PROJECT_ROOT / "mind_app" / "runtime" / "mcp" / "tool_progress.py"
    ).is_file(), "MCP tool progress source is missing"


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


def test_presentation_output_has_no_legacy_package_or_imports() -> None:
    """确保单轮输出会话和 sink 已归入 presentation/output 边界。"""
    legacy_root = PROJECT_ROOT / "mind_app" / "output"
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
        "agent.application.turns.context",
        "agent.application.turns.commands",
        "agent.application.turns.environment",
        "agent.application.turns.compact_result",
        "agent.application.turns.run_result",
        "agent.application.turns.stream_outcome",
        "agent.application.turns.projections",
        "agent.application.hooks.context",
        "agent.application.turns.execution",
        "agent.application.agents.fork_context",
            "agent.application.config.settings",
            "agent.application.config.session_identity",
            "agent.domain.hooks",
            "agent.domain.policies",
            "agent.domain.hook_trust",
            "agent.domain.hook_matching",
        "agent.domain.agents",
        "agent.domain.tool_policy",
        "agent.domain.execution_policy",
        "agent.ports",
        "agent.ports.agent_messages",
        "agent.adapters.agents.messages",
        "agent.adapters.agents.execution",
        "agent.adapters.turns.root",
        "agent.harness.agents.control",
        "agent.harness.agents.delivery",
        "agent.harness.agents.registry",
        "agent.harness.hooks.runtime",
        "agent.harness.hooks.scope",
        "agent.harness.mcp.owner",
        "agent.harness.subscription.owner",
        "agent.harness.execution.subagent_runner",
        "agent.harness.execution.subagent_submission",
        "agent.ports.subscription",
        "agent.protocol.json_value",
        "agent.protocol",
        "agent.stores.approvals.ledger",
        "agent.stores.approvals.permissions",
        "agent.stores.agents.mailbox",
        "agent.stores.agents.graph",
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
        PROJECT_ROOT / "mind_app" / "controller.py",
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


def test_controller_does_not_expose_runtime_facades() -> None:
    controller_path = PROJECT_ROOT / "mind_app" / "controller.py"
    tree = ast.parse(
        controller_path.read_text(encoding="utf-8-sig"),
        filename=str(controller_path),
    )
    controller = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Mind"
    )
    methods = {
        node.name
        for node in controller.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assigned_attributes = {
        node.attr
        for node in ast.walk(controller)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
    }
    called_names = {
        node.func.id
        for node in ast.walk(controller)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    closes_borrowed_report = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "report"
        for node in ast.walk(controller)
    )

    assert not {
        "bind_server_manager",
        "bind_service_runtime_context",
        "calling",
        "cancel_service_runtime_startup",
        "keepalive_task_done",
        "pause_subscription_listener",
        "reboot_runtime",
        "require_service_runtime_context",
        "restart_external_mcp_runtime",
        "run_service_runtime_startup",
        "run_turn_lifecycle",
        "start_keepalive_supervisor",
        "start_config_service",
        "start_external_mcp_runtime",
        "start_subscription_listener",
        "stop_keepalive_supervisor",
        "stop_config_service",
        "stop_service_runtime",
        "stop_external_mcp_runtime",
        "stop_subscription_listener",
        "stream_turn",
    } & methods
    assert not (
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "calling.py"
    ).exists()
    assert "event_reports" not in assigned_attributes
    assert not {
        "_native_coding_close_tasks",
        "_service_start_lock",
        "_service_start_task",
        "config_service",
        "exec_policy_manager",
        "keepalive_stop",
        "keepalive_task",
        "native_coding",
        "server_manager",
        "service_runtime_context",
        "stop_runtime_on_exit",
        "user_shell",
    } & assigned_attributes
    assert "RunReport" not in called_names
    assert not closes_borrowed_report
