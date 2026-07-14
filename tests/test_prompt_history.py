# -*- coding: utf-8 -*-

from types import SimpleNamespace

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.keys import Keys

from mind_core.prompting.box import PromptToolkitBox


def press_history_key(box: PromptToolkitBox, key: Keys, buffer: Buffer) -> None:
    """触发输入框的历史导航按键。"""
    binding = next(
        item
        for item in box.key_bindings.bindings
        if item.keys == (key,)
    )
    binding.handler(SimpleNamespace(
        app=SimpleNamespace(current_buffer=buffer),
        arg=1
    ))


def test_history_navigation_restores_deleted_entry_in_same_prompt() -> None:
    """删除已还原的输入后仍可在当前会话中再次还原。"""
    box = PromptToolkitBox()
    box.history.append_string("first input")
    box.history.append_string("second input")
    buffer = Buffer(history=box.history)

    press_history_key(box, Keys.Up, buffer)

    assert buffer.text == "second input"

    buffer.document = Document("", cursor_position=0)
    press_history_key(box, Keys.Up, buffer)

    assert buffer.text == "second input"
    assert box.history.get_strings() == ["first input", "second input"]


def test_history_navigation_restores_draft_when_moving_down() -> None:
    """向下导航回到当前草稿并保留光标位置。"""
    box = PromptToolkitBox()
    box.history.append_string("previous input")
    buffer = Buffer(history=box.history)
    buffer.document = Document("draft", cursor_position=3)

    box._navigate_history(buffer, step=-1, count=1)
    box._navigate_history(buffer, step=1, count=1)

    assert buffer.text == "draft"
    assert buffer.cursor_position == 3


def test_history_navigation_filters_candidates_by_current_prefix() -> None:
    """历史导航按当前光标前的输入过滤候选。"""
    box = PromptToolkitBox()
    box.history.append_string("first input")
    box.history.append_string("second input")
    buffer = Buffer(history=box.history)
    buffer.document = Document("sec", cursor_position=3)

    box._navigate_history(buffer, step=-1, count=1)

    assert buffer.text == "second input"
