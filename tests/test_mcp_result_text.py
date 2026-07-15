# -*- coding: utf-8 -*-

import ast
import re
from pathlib import Path

from backend.models.model_device import ActionResult
from backend.mcp_hub.hub_device.device import Device
from backend.utilities.validation import marked


ROOT = Path(__file__).resolve().parents[1]
RESULT_SOURCE_FILES = (
    "backend/mcp_core/core_framix.py",
    "backend/mcp_core/core_k6.py",
    "backend/mcp_core/core_memrix.py",
    "backend/mcp_hub/hub_device/device.py",
    "backend/mcp_hub/hub_medias.py",
    "backend/mcp_hub/hub_monkey.py",
    "backend/mcp_hub/hub_record.py",
)
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


class ResultTextExpressionVisitor(ast.NodeVisitor):
    """收集 MCP 结果构造中直接声明的 text 表达式。"""

    def __init__(self, source: str) -> None:
        self.source = source
        self.expressions: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg == "text":
                self._append_expression(keyword.value)

        name = self._call_name(node.func)
        if node.args and name in {
            "build_pack",
            "ensure_f",
            "except_tip",
            "fail_tip",
            "_inline_script_fail",
        }:
            self._append_expression(node.args[-1] if name == "ensure_f" else node.args[0])
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "text":
                self._append_expression(value)
        self.generic_visit(node)

    def _append_expression(self, node: ast.AST) -> None:
        expression = ast.get_source_segment(self.source, node)
        if expression:
            self.expressions.append(expression)

    @staticmethod
    def _call_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""


def _assert_english_text(value: str) -> None:
    assert not CJK_PATTERN.search(value), value


def test_mcp_result_text_expressions_have_no_chinese_literals() -> None:
    """受控 MCP text 表达式不包含中文文本字面量。"""
    for relative_path in RESULT_SOURCE_FILES:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        visitor = ResultTextExpressionVisitor(source)
        visitor.visit(ast.parse(source))

        for expression in visitor.expressions:
            _assert_english_text(expression)


def test_device_helper_result_texts_are_english() -> None:
    """设备辅助函数生成的状态文本保持英文。"""
    actions = (
        ActionResult.fail("timeout"),
        ActionResult.success(stage="already"),
        ActionResult.success(stage="retry"),
        ActionResult.success(),
        ActionResult.fail("xpath_not_supported"),
        ActionResult.fail("wm_size_unavailable"),
        ActionResult.fail("scroll_fail"),
        ActionResult.fail("stable_stop"),
        ActionResult.fail("max_swipes_reached"),
    )

    for action in actions:
        _assert_english_text(Device._foreground_text(action))
        _assert_english_text(Device._scroll_into_view_text(action))


def test_shared_validation_failures_are_english(tmp_path) -> None:
    """共享校验错误不带中文文案。"""
    missing = tmp_path / "missing.mp4"

    try:
        marked.ensure_f(missing, "input_file")
    except RuntimeError as exc:
        _assert_english_text(str(exc))
    else:
        raise AssertionError("ensure_f should fail for a missing file")

    _assert_english_text(str(marked.port_busy(1234, "liveness")))
