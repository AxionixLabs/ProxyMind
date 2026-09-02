# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = frozenset({
    ".git",
    "__pycache__",
    "backend",
    "codex-main",
    "node_modules",
    "schematic",
    "tests",
    "venv",
})
HEADER = (
    "# -*- coding: utf-8 -*-",
    "# Notes: ==== Mind™ ====",
)


def _python_files() -> tuple[Path, ...]:
    """返回规则覆盖范围内的 Python 文件。"""
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        relative_parts = path.relative_to(ROOT).parts
        if any(part in EXCLUDED_DIRS for part in relative_parts):
            continue
        files.append(path)
    return tuple(sorted(files))


def _is_main_guard(node: ast.stmt) -> bool:
    """判断顶层语句是否是 __main__ 入口。"""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(
            isinstance(comparator, ast.Constant)
            and comparator.value == "__main__"
            for comparator in test.comparators
        )
    )


def _is_test_file(path: Path) -> bool:
    """判断文件是否属于测试入口。"""
    return path.name.startswith("test_")


def _check_file(path: Path) -> list[str]:
    """检查单个 Python 文件的头部和生命周期入口。"""
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    relative = path.relative_to(ROOT)

    if tuple(lines[:2]) != HEADER:
        errors.append(f"{relative}: missing required header")

    if path.name == "__init__.py" or _is_test_file(path):
        return errors

    try:
        tree = ast.parse("\n".join(lines), filename=str(relative))
    except SyntaxError as error:
        errors.append(f"{relative}: syntax error: {error}")
        return errors

    guards = [node for node in tree.body if _is_main_guard(node)]
    if len(guards) != 1:
        errors.append(
            f"{relative}: expected exactly one __main__ guard, found {len(guards)}"
        )
        return errors

    guard = guards[0]
    if tree.body[-1] is not guard:
        errors.append(f"{relative}: __main__ guard must be the final top-level statement")

    guard_line = guard.lineno
    if guard_line < 3 or lines[guard_line - 2].strip() or lines[guard_line - 3].strip():
        errors.append(f"{relative}: __main__ guard requires two blank lines above it")

    return errors


def check() -> tuple[str, ...]:
    """校验非测试 Python 文件的统一头部和底部入口。"""
    errors = [
        error
        for path in _python_files()
        for error in _check_file(path)
    ]
    return tuple(errors)


def main() -> int:
    """运行 Python 文件布局检查。"""
    errors = check()
    if errors:
        print("python layout check failed:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("python layout check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
