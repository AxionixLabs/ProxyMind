# -*- coding: utf-8 -*-

"""审计前端、终端展示、交互状态和宿主适配器的所有权。

这些检查共同描述前端依赖图，维持整体可避免重复定义边界清单。
"""


import ast
from tests.architecture.source_inventory import (
    PROJECT_ROOT,
    all_python_sources as _all_python_sources,
    forbidden_imports as _forbidden_imports,
    forbidden_module_imports as _forbidden_module_imports,
    parsed_source as _parsed_source,
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
        and node.value.id == "host"
        and node.attr in {"turn_execution_runtime", "root_turn_session"}
    }
    assert not forbidden_attributes


def test_frontend_host_names_are_not_brand_bound() -> None:
    """确保前端组合对象不再使用产品名作为通用变量或成员名。"""
    violations: list[str] = []
    for module_path in (PROJECT_ROOT / "frontends").rglob("*.py"):
        tree = ast.parse(
            module_path.read_text(encoding="utf-8-sig"),
            filename=str(module_path),
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.arg) and node.arg == "mind":
                violations.append(f"{module_path.relative_to(PROJECT_ROOT)}: argument")
            elif isinstance(node, ast.Name) and node.id == "mind":
                violations.append(f"{module_path.relative_to(PROJECT_ROOT)}: name")
            elif isinstance(node, ast.Attribute) and node.attr == "mind":
                violations.append(f"{module_path.relative_to(PROJECT_ROOT)}: attribute")

    assert not violations, "brand-bound frontend host names remain:\n" + "\n".join(
        sorted(set(violations))
    )


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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
        tree = _parsed_source(path)
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
        tree = _parsed_source(path)
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
    assert "IdleStatusPort" not in finalizer_source
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
        "keyboard.py",
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
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
        tree = _parsed_source(path)
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
        tree = _parsed_source(path)
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

    conversation_path = (
        PROJECT_ROOT / "frontends" / "tui" / "features" / "conversation.py"
    )
    conversation_source = conversation_path.read_text(encoding="utf-8-sig")
    conversation_tree = ast.parse(
        conversation_source,
        filename=str(conversation_path),
    )
    fork_function = next(
        node
        for node in conversation_tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "fork_current_conversation"
    )
    assert [argument.arg for argument in fork_function.args.args[:2]] == [
        "host",
        "protocol_client",
    ]
    assert not fork_function.args.defaults
    fork_assignments = {
        node.target.id
        for node in conversation_tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    fork_assignments.update(
        target.id
        for node in conversation_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    )
    assert "request_conversation_fork" not in fork_assignments
    assert "protocol_client is None" not in conversation_source

    mandatory_protocol_entries = (
        (
            PROJECT_ROOT / "frontends" / "cli" / "dispatch.py",
            "run_selected_command",
        ),
        (
            PROJECT_ROOT / "frontends" / "tui" / "session" / "loop.py",
            "run_tui_loop",
        ),
    )
    for module_path, function_name in mandatory_protocol_entries:
        module_tree = ast.parse(
            module_path.read_text(encoding="utf-8-sig"),
            filename=str(module_path),
        )
        function = next(
            node
            for node in module_tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == function_name
        )
        protocol_index = next(
            index
            for index, argument in enumerate(function.args.kwonlyargs)
            if argument.arg == "protocol_client"
        )
        protocol_argument = function.args.kwonlyargs[protocol_index]
        assert function.args.kw_defaults[protocol_index] is None
        assert protocol_argument.annotation is not None
        assert ast.unparse(protocol_argument.annotation) == "ProtocolCommandClient"

    dispatcher_path = (
        PROJECT_ROOT / "frontends" / "tui" / "session" / "dispatch.py"
    )
    dispatcher_tree = ast.parse(
        dispatcher_path.read_text(encoding="utf-8-sig"),
        filename=str(dispatcher_path),
    )
    dispatcher_class = next(
        node
        for node in dispatcher_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "TuiCommandDispatcher"
    )
    dispatcher_initializer = next(
        node
        for node in dispatcher_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "__init__"
    )
    dispatcher_protocol_index = next(
        index
        for index, argument in enumerate(dispatcher_initializer.args.kwonlyargs)
        if argument.arg == "protocol_client"
    )
    dispatcher_protocol = dispatcher_initializer.args.kwonlyargs[
        dispatcher_protocol_index
    ]
    assert dispatcher_initializer.args.kw_defaults[
        dispatcher_protocol_index
    ] is None
    assert dispatcher_protocol.annotation is not None
    assert ast.unparse(dispatcher_protocol.annotation) == "ProtocolCommandClient"

    for feature_name in (
        "agents.py",
        "helix.py",
        "listener.py",
        "mailbox.py",
        "mcp.py",
        "processes.py",
        "shell.py",
    ):
        feature_source = (
            PROJECT_ROOT / "frontends" / "tui" / "features" / feature_name
        ).read_text(encoding="utf-8-sig")
        assert "mind: typing.Any" not in feature_source
        assert 'getattr(controller, "conversation"' not in feature_source
        assert 'getattr(host, "animate"' not in feature_source
        assert 'getattr(host.workspace_runtime, "coding"' not in feature_source
        assert 'getattr(host.frontend.application, "viewport"' not in feature_source
        assert (
            'getattr(controller.frontend.application, "viewport"'
            not in feature_source
        )

    mcp_feature_source = (
        PROJECT_ROOT / "frontends" / "tui" / "features" / "mcp.py"
    ).read_text(encoding="utf-8-sig")
    assert "runtime.tool_groups" in mcp_feature_source
    assert "runtime.group" not in mcp_feature_source
    assert 'getattr(runtime, "group"' not in mcp_feature_source
    assert "server_stats" not in mcp_feature_source

    tools_feature_source = (
        PROJECT_ROOT / "frontends" / "tui" / "features" / "tools.py"
    ).read_text(encoding="utf-8-sig")
    assert "display_name_for_tool" in tools_feature_source
    assert "external_group" not in tools_feature_source
    assert 'getattr(application, "viewport"' not in tools_feature_source

    mcp_session_source = (
        PROJECT_ROOT / "agent" / "ports" / "mcp_session.py"
    ).read_text(encoding="utf-8-sig")
    assert "def display_name_for_tool" in mcp_session_source

    helix_runtime_source = (
        PROJECT_ROOT / "frontends" / "helix" / "runtime.py"
    ).read_text(encoding="utf-8-sig")
    assert 'getattr(mind.service_runtime, "ensure_ready"' not in helix_runtime_source

    loop_source = (
        PROJECT_ROOT / "frontends" / "tui" / "session" / "loop.py"
    ).read_text(encoding="utf-8-sig")
    assert 'getattr(mind, "attach"' not in loop_source

    barrier_source = (
        PROJECT_ROOT / "frontends" / "tui" / "session" / "barriers.py"
    ).read_text(encoding="utf-8-sig")
    assert 'getattr(external_mcp, "started"' not in barrier_source

    tool_runtime_source = (
        PROJECT_ROOT / "agent" / "ports" / "tool_runtime.py"
    ).read_text(encoding="utf-8-sig")
    assert "server_stats" not in tool_runtime_source

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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
        tree = _parsed_source(path)
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
        tree = _parsed_source(path)
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
        "ApprovalCompleted",
        "ApprovalReviewCompleted",
        "ApprovalReviewStarted",
        "ApprovalStarted",
            "AssistantBuffered",
            "AssistantSettled",
            "AssistantVisible",
            "ModelWaitRequested",
        "OutputActivityPort",
        "OutputControlPort",
        "OutputPort",
        "OutputPresentationPort",
        "OutputSession",
        "OutputSessionFactory",
        "OutputSurfaceContext",
        "PassiveOutputActivity",
        "PresentationSuperseded",
        "RecoveryChanged",
        "RetryChanged",
        "SurfaceClosed",
        "SurfaceTurnStarted",
        "TerminalWaitCompleted",
        "TerminalWaitStarted",
        "ToolBatchCompleted",
        "ToolBatchStarted",
        "ToolCompleted",
        "ToolInteractionActivityPort",
        "ToolStarted",
        "TurnTerminal",
        "_ApprovalActivityEvent",
        "_ApprovalReviewActivityEvent",
        "_AssistantActivityEvent",
        "_BatchActivityEvent",
        "_ScopedActivityEvent",
        "_TerminalWaitActivityEvent",
        "_ToolActivityEvent",
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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
    for path in _all_python_sources():
        tree = _parsed_source(path)
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

    assert not violations, "legacy approval state imports remain:\n" + "\n".join(violations)
