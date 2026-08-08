# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.keys import Keys

from mind_app.interaction.contracts import PromptContext
from mind_app.tui.core.input import TuiInputModel
from mind_app.tui.core.runtime import TuiRuntime


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


async def submit(runtime: TuiRuntime) -> str:
    runtime.screen.input.buffer.validate_and_handle()
    return await runtime.read_message(PromptContext(model="test"))


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
    assert [entry.visible_text for entry in model.history.entries()] == [
        "first input"
    ]


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


@pytest.mark.anyio
async def test_folded_paste_history_recall_resubmits_original_text() -> None:
    runtime = TuiRuntime()
    original = "long pasted context " * 80
    placeholder = runtime.input_model._display_paste(original, "")
    buffer = runtime.screen.input.buffer
    buffer.text = placeholder

    assert await submit(runtime) == original.strip()
    assert runtime.input_model.history.get_strings() == [placeholder]

    runtime.input_model._navigate_history(buffer, step=-1, count=1)

    assert buffer.text == placeholder
    assert runtime.input_model.restore_submission(buffer.text) == original.strip()
    assert await submit(runtime) == original.strip()


@pytest.mark.anyio
async def test_identical_paste_labels_restore_each_history_original() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    originals = ("a" * 1200, "b" * 1200)

    for original in originals:
        placeholder = runtime.input_model._display_paste(original, "")
        assert placeholder == "[Pasted Content 1200 chars]"
        buffer.text = placeholder
        assert await submit(runtime) == original

    assert runtime.input_model.history.get_strings() == [
        "[Pasted Content 1200 chars]",
        "[Pasted Content 1200 chars]",
    ]

    runtime.input_model._navigate_history(buffer, step=-1, count=1)
    assert runtime.input_model.restore_submission(buffer.text) == originals[1]

    runtime.input_model._navigate_history(buffer, step=-1, count=1)
    assert runtime.input_model.restore_submission(buffer.text) == originals[0]


@pytest.mark.anyio
async def test_multiple_folded_pastes_keep_surrounding_editable_text() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    first_original = "first pasted section " * 70
    second_original = "second pasted section " * 65
    first = runtime.input_model._display_paste(first_original, "before\n")
    middle = f"before\n{first}\nbetween\n"
    second = runtime.input_model._display_paste(second_original, middle)
    editable = f"{middle}{second}\nafter"
    expected = (
        f"before\n{first_original}\nbetween\n{second_original}\nafter"
    )
    buffer.text = editable

    assert await submit(runtime) == expected

    runtime.input_model._navigate_history(buffer, step=-1, count=1)

    assert buffer.text == editable
    assert set(runtime.input_model.submission_state()) == {first, second}
    assert await submit(runtime) == expected


@pytest.mark.anyio
async def test_history_navigation_restores_folded_paste_draft_state() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    historical = "historical paste " * 80
    historical_placeholder = runtime.input_model._display_paste(
        historical,
        "review ",
    )
    historical_editable = f"review {historical_placeholder} old"
    buffer.text = historical_editable
    assert await submit(runtime) == f"review {historical} old"

    draft = "draft paste " * 100
    draft_placeholder = runtime.input_model._display_paste(draft, "")
    draft_text = f"review {draft_placeholder} later"
    buffer.document = Document(draft_text, cursor_position=7)

    runtime.input_model._navigate_history(buffer, step=-1, count=1)
    assert buffer.text == historical_editable
    assert runtime.input_model.restore_submission(buffer.text) == (
        f"review {historical} old"
    )

    runtime.input_model._navigate_history(buffer, step=1, count=1)
    assert buffer.text == draft_text
    assert buffer.cursor_position == 7
    assert runtime.input_model.restore_submission(buffer.text) == (
        f"review {draft} later"
    )


@pytest.mark.anyio
async def test_shell_history_restores_folded_paste_for_resubmission() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    original = "shell argument " * 100
    placeholder = runtime.input_model._display_paste(original, "")
    runtime.input_model.set_shell_mode(True)
    buffer.text = placeholder

    assert await submit(runtime) == f"! {original.strip()}"
    assert runtime.input_model.history.get_strings() == [f"! {placeholder}"]

    runtime.input_model._navigate_history(buffer, step=-1, count=1)

    assert runtime.input_model.shell_mode
    assert buffer.text == placeholder
    assert await submit(runtime) == f"! {original.strip()}"


@pytest.mark.anyio
async def test_submission_history_discards_deleted_paste_mapping() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    original = "removed paste " * 100
    placeholder = runtime.input_model._display_paste(original, "")
    buffer.text = placeholder
    buffer.text = "keep only text"

    assert await submit(runtime) == "keep only text"

    entry = runtime.input_model.history.entries()[-1]
    assert entry.editable_text == "keep only text"
    assert entry.paste_store == {}


@pytest.mark.anyio
async def test_plain_duplicate_submission_keeps_single_history_entry() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer

    for _ in range(2):
        buffer.text = "same query"
        assert await submit(runtime) == "same query"

    assert runtime.input_model.history.get_strings() == ["same query"]
