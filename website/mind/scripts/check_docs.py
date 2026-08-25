# -*- coding: utf-8 -*-
"""校验命令文档、官网清单和生成页之间的契约。"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parents[3]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sync_docs import (  # noqa: E402
    MD_LINK_RE,
    load_manifest,
    resolve_roots,
)


SLASH_DOC = "docs/interactive-mode.md"
CLI_DOC = "docs/cli-usage.md"
SLASH_SOURCE = "mind_app/tui/prompting/commands.py"


def _literal_string(node: ast.AST) -> str | None:
    """读取 AST 中的字符串字面量。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _slash_command_names(source_root: Path) -> tuple[str, ...]:
    """从 TUI 命令注册表读取规范名称和可执行别名。"""
    source = source_root / SLASH_SOURCE
    if not source.exists():
        raise FileNotFoundError(f"slash command source not found: {source}")

    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    names: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue

        target_nodes = (
            node.targets
            if isinstance(node, ast.Assign)
            else (node.target,)
        )
        if not any(
            isinstance(target, ast.Name) and target.id == "TUI_COMMANDS"
            for target in target_nodes
        ):
            continue

        value = node.value
        if not isinstance(value, ast.Tuple):
            continue

        for item in value.elts:
            if not isinstance(item, ast.Call) or len(item.args) < 2:
                continue

            command = _literal_string(item.args[1])
            if command:
                names.append(command)

            for keyword in item.keywords:
                if keyword.arg != "aliases" or not isinstance(keyword.value, ast.Tuple):
                    continue
                names.extend(
                    alias
                    for alias_node in keyword.value.elts
                    if (alias := _literal_string(alias_node))
                )

        break

    if not names:
        raise ValueError(f"no TUI commands found in {source}")
    return tuple(dict.fromkeys(names))


def _cli_command_paths(source_root: Path) -> tuple[str, ...]:
    """从 CLI parser 的已登记帮助路径读取命令层级。"""
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    from mind_app.cli.arguments import create_cli_parser

    paths = create_cli_parser().registered_command_parsers()
    return tuple(
        "mind " + " ".join(path)
        for path in paths
        if path != ("e",)
    )


def _missing_tokens(document: Path, tokens: tuple[str, ...]) -> tuple[str, ...]:
    """返回文档中缺失的命令文本。"""
    text = document.read_text(encoding="utf-8")
    return tuple(token for token in tokens if token not in text)


def _validate_manifest(source_root: Path, site_root: Path) -> list[str]:
    """校验 manifest 的源文件和目标文件定义。"""
    errors: list[str] = []
    entries = load_manifest(site_root)

    for entry in entries:
        source = source_root / entry.source
        if not source.exists():
            errors.append(f"manifest source missing: {entry.source}")

    return errors


def _validate_generated_pages(site_root: Path) -> list[str]:
    """校验官网生成页存在且内部 Markdown 链接可解析。"""
    target_root = site_root / "pages" / "generated"
    errors: list[str] = []
    entries = load_manifest(site_root)

    for entry in entries:
        target = target_root / entry.target
        if not target.exists():
            errors.append(f"generated page missing: {entry.target}")

    if not target_root.exists():
        return errors

    for page in target_root.glob("*.md"):
        text = page.read_text(encoding="utf-8")
        for match in MD_LINK_RE.finditer(text):
            path = match.group("path")
            target = page.parent / path
            if not target.exists():
                errors.append(
                    f"broken generated link: {page.name} -> {path}"
                )

    return errors


def validate(*, generated: bool = False) -> tuple[str, ...]:
    """执行命令覆盖、manifest 和可选生成页校验。"""
    source_root, site_root = resolve_roots()
    errors = _validate_manifest(source_root, site_root)

    slash_doc = source_root / SLASH_DOC
    cli_doc = source_root / CLI_DOC

    missing_slash = _missing_tokens(slash_doc, _slash_command_names(source_root))
    errors.extend(f"slash command missing from {SLASH_DOC}: {name}" for name in missing_slash)

    missing_cli = _missing_tokens(cli_doc, _cli_command_paths(source_root))
    errors.extend(f"CLI command missing from {CLI_DOC}: {name}" for name in missing_cli)

    if generated:
        errors.extend(_validate_generated_pages(site_root))

    return tuple(errors)


def main() -> int:
    """运行文档校验并输出可操作的失败原因。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generated",
        action="store_true",
        help="同时校验 pages/generated 中的页面和内部链接",
    )
    arguments = parser.parse_args()

    errors = validate(generated=arguments.generated)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    suffix = " and generated pages" if arguments.generated else ""
    print(f"documentation contract ok for commands{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
