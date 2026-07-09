# -*- coding: utf-8 -*-

from mind_app.modes.support.repl_prompt import primary_model_prompt_label


def test_primary_model_prompt_label_includes_effort_when_model_exists() -> None:
    """模型不为空时 prompt 标签包含 reasoning effort。"""
    assert primary_model_prompt_label({
        "primary": {
            "model": "gpt-5.5",
            "reasoning_effort": "high"
        }
    }) == "gpt-5.5 high"


def test_primary_model_prompt_label_omits_effort_without_model() -> None:
    """模型为空时 prompt 标签不展示 effort。"""
    assert primary_model_prompt_label({
        "primary": {
            "model": "",
            "reasoning_effort": "high"
        }
    }) == ""


def test_primary_model_prompt_label_uses_fallback_model() -> None:
    """配置缺少 model 字段时使用当前模型回退值。"""
    assert primary_model_prompt_label({
        "primary": {
            "reasoning_effort": "medium"
        }
    }, "gpt-5") == "gpt-5 medium"
