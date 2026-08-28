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


def test_transport_protocol_package_is_independent() -> None:
    violations = _forbidden_imports(
        "mind_nova",
        {"backend", "engine", "mind_app", "mind_core", "server"},
    )

    assert not violations, "mind_nova crosses its package boundary:\n" + "\n".join(
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
        {"engine", "mind_app", "mind_core", "mind_nova", "server"},
    )

    assert not violations, "backend imports application code:\n" + "\n".join(
        violations
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

    assert not {
        "calling",
        "pause_subscription_listener",
        "restart_external_mcp_runtime",
        "run_turn_lifecycle",
        "start_external_mcp_runtime",
        "start_subscription_listener",
        "stop_external_mcp_runtime",
        "stop_subscription_listener",
        "stream_turn",
    } & methods
    assert not (
        PROJECT_ROOT / "mind_app" / "runtime" / "support" / "calling.py"
    ).exists()
