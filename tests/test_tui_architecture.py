# -*- coding: utf-8 -*-

import ast
from pathlib import Path


TUI_PACKAGE = Path(__file__).parents[1] / "frontends" / "tui"


def test_rendering_does_not_import_core() -> None:
    """保证 rendering 层不会重新依赖旧 core 实现。"""
    violations: list[str] = []
    rendering = TUI_PACKAGE / "rendering"

    for path in rendering.rglob("*.py"):
        module_parts = path.relative_to(TUI_PACKAGE).with_suffix("").parts
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
                if any(name.startswith("frontends.tui.core") for name in names):
                    violations.append(str(path.relative_to(TUI_PACKAGE)))
            elif isinstance(node, ast.ImportFrom):
                target = _resolved_import(module_parts, node.level, node.module)
                if target[:1] == ("core",):
                    violations.append(str(path.relative_to(TUI_PACKAGE)))

    assert not violations, f"rendering imports core: {sorted(set(violations))}"


def test_runtime_state_does_not_import_screen_or_runtime() -> None:
    """保证运行时状态协作者不反向耦合 Screen 和具体 Runtime。"""
    violations: list[str] = []
    runtime = TUI_PACKAGE / "runtime"

    for path in runtime.rglob("*.py"):
        module_parts = path.relative_to(TUI_PACKAGE).with_suffix("").parts
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    alias.name.startswith("frontends.tui.core.runtime")
                    or alias.name.startswith("frontends.tui.core.screen")
                    for alias in node.names
                ):
                    violations.append(str(path.relative_to(TUI_PACKAGE)))
            elif isinstance(node, ast.ImportFrom):
                target = _resolved_import(module_parts, node.level, node.module)
                if target[:2] == ("core", "runtime") or target[:2] == (
                    "core",
                    "screen",
                ):
                    violations.append(str(path.relative_to(TUI_PACKAGE)))

    assert not violations, (
        f"runtime state imports screen/runtime: {sorted(set(violations))}"
    )


def test_resume_runtime_coordinator_uses_narrow_ports() -> None:
    """保证 Resume coordinator 不依赖具体 Screen、Runtime 或 Viewport。"""
    path = TUI_PACKAGE / "runtime" / "resume_picker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_parts = tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts)
    forbidden: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            forbidden.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith((
                    "frontends.tui.core.screen",
                    "frontends.tui.core.runtime",
                    "frontends.tui.core.viewport",
                ))
            )
        elif isinstance(node, ast.ImportFrom):
            target = _resolved_import(module_parts, node.level, node.module)
            if target[:2] in {
                ("core", "screen"),
                ("core", "runtime"),
                ("core", "viewport"),
            }:
                forbidden.append(".".join(target))

    assert not forbidden


def test_process_feature_uses_capability_port() -> None:
    """保证进程 feature 不再把具体 TuiRuntime 作为类型依赖。"""
    path = TUI_PACKAGE / "features" / "processes.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.startswith("frontends.tui.core.runtime")
                for alias in node.names
            ):
                violations.append("absolute core.runtime import")
        elif isinstance(node, ast.ImportFrom):
            target = _resolved_import(
                tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
                node.level,
                node.module,
            )
            if target[:2] == ("core", "runtime"):
                violations.append("relative core.runtime import")

    assert not violations, f"process feature imports concrete runtime: {violations}"
    assert any(
        isinstance(node, ast.ImportFrom)
        and _resolved_import(
            tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
            node.level,
            node.module,
        )[:2] == ("runtime", "ports")
        for node in ast.walk(tree)
    )


def test_foreground_barrier_uses_capability_port() -> None:
    """保证 session 屏障不再把具体 TuiRuntime 作为类型依赖。"""
    path = TUI_PACKAGE / "session" / "barriers.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.startswith("frontends.tui.core.runtime")
                for alias in node.names
            ):
                violations.append("absolute core.runtime import")
        elif isinstance(node, ast.ImportFrom):
            target = _resolved_import(
                tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
                node.level,
                node.module,
            )
            if target[:2] == ("core", "runtime"):
                violations.append("relative core.runtime import")

    assert not violations, f"foreground barrier imports concrete runtime: {violations}"
    assert any(
        isinstance(node, ast.ImportFrom)
        and _resolved_import(
            tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
            node.level,
            node.module,
        )[:2] == ("runtime", "ports")
        for node in ast.walk(tree)
    )


def test_turn_execution_uses_capability_port() -> None:
    """保证单轮执行不再把具体 TuiRuntime 作为类型依赖。"""
    path = TUI_PACKAGE / "session" / "turn.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.startswith("frontends.tui.core.runtime")
                for alias in node.names
            ):
                violations.append("absolute core.runtime import")
        elif isinstance(node, ast.ImportFrom):
            target = _resolved_import(
                tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
                node.level,
                node.module,
            )
            if target[:2] == ("core", "runtime"):
                violations.append("relative core.runtime import")

    assert not violations, f"turn execution imports concrete runtime: {violations}"
    assert any(
        isinstance(node, ast.ImportFrom)
        and _resolved_import(
            tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
            node.level,
            node.module,
        )[:2] == ("runtime", "ports")
        for node in ast.walk(tree)
    )


def test_turn_input_uses_capability_port() -> None:
    """保证轮次输入对账不再把具体 TuiRuntime 作为类型依赖。"""
    path = TUI_PACKAGE / "session" / "turn_input.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.startswith("frontends.tui.core.runtime")
                for alias in node.names
            ):
                violations.append("absolute core.runtime import")
        elif isinstance(node, ast.ImportFrom):
            target = _resolved_import(
                tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
                node.level,
                node.module,
            )
            if target[:2] == ("core", "runtime"):
                violations.append("relative core.runtime import")

    assert not violations, f"turn input imports concrete runtime: {violations}"
    assert any(
        isinstance(node, ast.ImportFrom)
        and _resolved_import(
            tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
            node.level,
            node.module,
        )[:2] == ("runtime", "ports")
        for node in ast.walk(tree)
    )


def test_read_only_features_use_capability_ports() -> None:
    """保证只读菜单 feature 不再把具体 TuiRuntime 作为类型依赖。"""
    expected = {
        "features/history.py": "ResumePickerPort",
        "features/model.py": "MenuSelectionPort",
        "features/skills.py": "SkillRuntimePort",
    }

    for relative_path, expected_port in expected.items():
        path = TUI_PACKAGE / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations: list[str] = []
        imported_ports: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    alias.name.startswith("frontends.tui.core.runtime")
                    for alias in node.names
                ):
                    violations.append("absolute core.runtime import")
            elif isinstance(node, ast.ImportFrom):
                target = _resolved_import(
                    tuple(path.relative_to(TUI_PACKAGE).with_suffix("").parts),
                    node.level,
                    node.module,
                )
                if target[:2] == ("core", "runtime"):
                    violations.append("relative core.runtime import")
                if target[:2] == ("runtime", "ports"):
                    imported_ports.update(
                        alias.name for alias in node.names
                    )

        assert not violations, (
            f"{relative_path} imports concrete runtime: {violations}"
        )
        assert expected_port in imported_ports


def _resolved_import(
    module_parts: tuple[str, ...],
    level: int,
    module: str | None,
) -> tuple[str, ...]:
    """把 frontends.tui 内的相对导入解析为包内路径。"""
    imported = tuple((module or "").split(".")) if module else ()
    if level <= 0:
        prefix = ("mind_app", "tui")
        full = imported
        return full[len(prefix):] if full[:len(prefix)] == prefix else full
    package = module_parts[:-1]
    return package[:max(0, len(package) - level + 1)] + imported
