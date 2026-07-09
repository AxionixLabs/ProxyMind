# -*- coding: utf-8 -*-

from mind_app.modes.support.repl_model import render_model_effort_menu
from mind_app.modes.support.repl_prompt import normalize_reasoning_effort


def test_normalize_model_effort_uses_default_for_invalid_value() -> None:
    """非法 effort 使用默认档位。"""
    assert normalize_reasoning_effort("invalid") == "medium"


def test_render_model_effort_menu_uses_chinese_descriptions() -> None:
    """模型 effort 菜单使用中文选项说明。"""
    text = "".join(part for _, part in render_model_effort_menu("high", 2))

    assert "Reasoning Effort" in text
    assert "current=high" in text
    assert "↑/↓ select · Enter apply · q cancel" in text
    assert "低推理，优先速度" in text
    assert "默认档位，平衡速度与质量" in text
    assert "高推理，提升复杂任务质量" in text
    assert "最高推理，适合困难任务" in text
