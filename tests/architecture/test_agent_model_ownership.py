# -*- coding: utf-8 -*-

"""审计 Agent 应用值对象、状态和持久化模型的唯一所有权。"""


import ast
from pathlib import Path
from tests.architecture.source_inventory import (
    PROJECT_ROOT,
    all_python_sources as _all_python_sources,
    forbidden_imports as _forbidden_imports,
    parsed_source as _parsed_source,
)


def test_hook_models_have_application_ownership() -> None:
    """确保 Hook 运行值对象不再由 runtime 目录持有。"""
    legacy_path = PROJECT_ROOT / "mind_app" / "runtime" / "hooks" / "models.py"
    assert not legacy_path.is_file(), "legacy hook model source still exists"

    legacy_modules = {"mind_app.runtime.hooks.models"}
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
        "agent.harness.execution.idle_status",
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
    assert not idle_status_path.is_file(), "obsolete Harness idle status remains"
    execution_violations = _forbidden_imports(
        "agent/harness/execution",
        {"backend", "engine", "frontends", "infrastructure", "mind_app", "mind_core", "server"},
    )
    assert not execution_violations, (
        "Harness execution crosses its boundary:\n"
        + "\n".join(execution_violations)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
