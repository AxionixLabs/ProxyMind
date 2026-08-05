# -*- coding: utf-8 -*-

from types import SimpleNamespace

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.keys import Keys

from mind_app.tui.core.input import TuiInputModel


def press_history_key(model: TuiInputModel, key: Keys, buffer: Buffer) -> None:
    binding = next(
        item
        for item in model.key_bindings.bindings
        if item.keys == (key,)
    )
    binding.handler(SimpleNamespace(
        app=SimpleNamespace(current_buffer=buffer),
        arg=1,
    ))


def test_deleted_history_entry_is_available_without_submit() -> None:
    model = TuiInputModel()
    model.history.append_string("first input")
    model.history.append_string("second input")
    buffer = Buffer(history=model.history)

    press_history_key(model, Keys.Up, buffer)
    assert buffer.text == "second input"

    buffer.document = Document("", cursor_position=0)
    press_history_key(model, Keys.Up, buffer)

    assert buffer.text == "second input"
    assert model.history.get_strings() == ["first input", "second input"]


def test_rollback_latest_removes_storage_and_navigation_entry() -> None:
    model = TuiInputModel()
    model.history.append_string("first input")
    model.history.append_string("queued input")

    model.rollback_submission_history("queued input")

    assert model.history.get_strings() == ["first input"]
    assert list(model.history.load_history_strings()) == ["first input"]


def test_history_navigation_restores_draft_and_cursor() -> None:
    model = TuiInputModel()
    model.history.append_string("previous input")
    buffer = Buffer(history=model.history)
    buffer.document = Document("draft", cursor_position=3)

    model._navigate_history(buffer, step=-1, count=1)
    model._navigate_history(buffer, step=1, count=1)

    assert buffer.text == "draft"
    assert buffer.cursor_position == 3


def test_history_navigation_filters_by_current_prefix() -> None:
    model = TuiInputModel()
    model.history.append_string("first input")
    model.history.append_string("second input")
    buffer = Buffer(history=model.history)
    buffer.document = Document("sec", cursor_position=3)

    model._navigate_history(buffer, step=-1, count=1)

    assert buffer.text == "second input"


def test_history_navigation_suppresses_slash_menu_until_edit() -> None:
    model = TuiInputModel()
    model.history.append_string("first query")
    model.history.append_string("/skills")
    buffer = Buffer(
        history=model.history,
        completer=model.completer,
        complete_while_typing=True,
    )

    press_history_key(model, Keys.Up, buffer)

    assert buffer.text == "/skills"
    assert model.completion_menu_completions(buffer.document) is None

    press_history_key(model, Keys.Up, buffer)

    assert buffer.text == "first query"

    press_history_key(model, Keys.Down, buffer)
    press_history_key(model, Keys.Left, buffer)

    assert buffer.text == "/skills"
    assert model.completion_menu_completions(buffer.document) is not None
    assert buffer.complete_state is not None
    assert [
        completion.display_text
        for completion in buffer.complete_state.completions
    ] == ["/skills"]


def test_shell_history_restores_prefix_mode() -> None:
    model = TuiInputModel()
    model.history.append_string("! rg TODO")
    buffer = Buffer(history=model.history)

    model._navigate_history(buffer, step=-1, count=1)

    assert model.shell_mode
    assert buffer.text == "rg TODO"

    buffer.document = Document("", cursor_position=0)
    model._navigate_history(buffer, step=-1, count=1)

    assert model.shell_mode
    assert buffer.text == "rg TODO"
