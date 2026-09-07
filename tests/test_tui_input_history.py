# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.clipboard import InMemoryClipboard
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.keys import Keys

from frontends.interaction.contracts import PromptContext
from frontends.tui.core.input import TuiInputModel
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime


def press_history_key(model: TuiInputModel, key: Keys, buffer: Buffer) -> None:
    app = SimpleNamespace(
        current_buffer=buffer,
        clipboard=InMemoryClipboard(),
        invalidate=lambda: None,
    )
    binding = next(
        item
        for item in model.key_bindings.bindings
        if item.keys == (key,)
    )
    binding.handler(SimpleNamespace(
        app=app,
        current_buffer=buffer,
        arg=1,
        is_repeat=False,
        key_sequence=(SimpleNamespace(key=key),),
    ))


def press_input_sequence(
    model: TuiInputModel,
    keys: tuple[Keys | str, ...],
    buffer: Buffer,
) -> None:
    app = SimpleNamespace(
        current_buffer=buffer,
        clipboard=InMemoryClipboard(),
        invalidate=lambda: None,
    )
    binding = next(
        item
        for item in model.key_bindings.bindings
        if item.keys == keys
    )
    binding.handler(SimpleNamespace(
        app=app,
        current_buffer=buffer,
        arg=1,
        is_repeat=False,
        key_sequence=tuple(
            SimpleNamespace(key=key)
            for key in keys
        ),
    ))


def test_ctrl_o_copies_last_response_without_editing_input() -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.text = "draft"
    copied: list[None] = []
    model.bind_copy_last_response(lambda: copied.append(None))

    press_history_key(model, Keys.ControlO, buffer)

    assert copied == [None]
    assert buffer.text == "draft"


def test_ctrl_j_inserts_newline_after_ctrl_o_is_reserved_for_copy() -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("firstsecond", cursor_position=5)
    binding = next(
        item
        for item in model.key_bindings.bindings
        if item.keys == (Keys.ControlJ,)
    )

    binding.handler(SimpleNamespace(
        app=SimpleNamespace(current_buffer=buffer),
        arg=1,
    ))

    assert buffer.text == "first\nsecond"
    assert buffer.cursor_position == 6


@pytest.mark.anyio
async def test_ctrl_o_pipe_input_routes_copy_without_mutating_draft() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        copied = asyncio.Event()
        runtime.bind_copy_last_response_handler(copied.set)

        await runtime.open()
        try:
            runtime.screen.input.buffer.text = "draft"
            pipe_input.send_text("\x0f")
            await asyncio.wait_for(copied.wait(), timeout=1)

            assert runtime.screen.input.buffer.text == "draft"
        finally:
            await runtime.close()


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

    assert buffer.text == "first input"
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


def test_history_navigation_ignores_interior_cursor_for_normal_up() -> None:
    model = TuiInputModel()
    model.history.append_string("first input")
    model.history.append_string("second input")
    buffer = Buffer(history=model.history)

    press_history_key(model, Keys.Up, buffer)
    press_history_key(model, Keys.Left, buffer)
    press_history_key(model, Keys.Up, buffer)

    assert buffer.text == "second input"
    assert buffer.cursor_position == len("second input") - 1


def test_history_navigation_stops_after_returning_to_nonempty_draft() -> None:
    model = TuiInputModel()
    model.history.append_string("first input")
    buffer = Buffer(history=model.history)
    buffer.document = Document("draft", cursor_position=len("draft"))

    press_history_key(model, Keys.Up, buffer)
    assert buffer.text == "draft"
    press_history_key(model, Keys.Down, buffer)
    press_history_key(model, Keys.Up, buffer)

    assert buffer.text == "draft"


def test_history_navigation_reopens_slash_menu_after_cursor_motion() -> None:
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


def test_history_slash_menu_filters_by_cursor_prefix() -> None:
    model = TuiInputModel()
    model.history.append_string("/permissions")
    buffer = Buffer(
        history=model.history,
        completer=model.completer,
        complete_while_typing=True,
    )

    press_history_key(model, Keys.Up, buffer)
    assert model.completion_menu_completions(buffer.document) is None

    for _ in range(10):
        press_history_key(model, Keys.Left, buffer)
    assert buffer.cursor_position == 2
    assert [
        completion.display_text
        for completion in buffer.complete_state.completions
    ] == ["/permissions", "/provider", "/preferences", "/ps"]

    press_history_key(model, Keys.Right, buffer)
    assert buffer.cursor_position == 3
    assert [
        completion.display_text
        for completion in buffer.complete_state.completions
    ] == ["/permissions"]

    for _ in range(3):
        press_history_key(model, Keys.Left, buffer)
    assert buffer.cursor_position == 0
    assert model.completion_menu_completions(buffer.document) is None


def test_slash_menu_requires_command_at_first_column() -> None:
    model = TuiInputModel()
    buffer = Buffer(
        completer=model.completer,
        complete_while_typing=True,
        document=Document(" /skills", cursor_position=len(" /skills")),
    )

    model.refresh_completion_menu(buffer)

    assert model.completion_menu_completions(buffer.document) is None
    assert buffer.complete_state is None


def test_history_down_clears_after_newest_entry() -> None:
    model = TuiInputModel()
    model.history.append_string("/skills")
    buffer = Buffer(
        history=model.history,
        completer=model.completer,
        complete_while_typing=True,
    )

    press_history_key(model, Keys.Up, buffer)
    assert buffer.text == "/skills"
    assert model.completion_menu_completions(buffer.document) is None

    press_history_key(model, Keys.Down, buffer)

    assert buffer.text == ""
    assert buffer.complete_state is None

    buffer.document = Document("/skills", cursor_position=len("/skills"))
    model.reopen_completion_menu(buffer)
    model.refresh_completion_menu(buffer)

    assert model.completion_menu_completions(buffer.document) is not None


def test_history_slash_dismissal_clears_after_editing_arguments() -> None:
    model = TuiInputModel()
    model.history.append_string("/skills")
    buffer = Buffer(
        history=model.history,
        completer=model.completer,
        complete_while_typing=True,
    )

    press_history_key(model, Keys.Up, buffer)
    buffer.document = Document("/skills ", cursor_position=len("/skills "))
    model.reopen_completion_menu(buffer)

    press_history_key(model, Keys.Left, buffer)

    assert model.completion_menu_completions(buffer.document) is not None


@pytest.mark.parametrize(
    ("text", "cursor_position", "expected_text", "expected_cursor"),
    (
        ("first\nsecond\nthird", 2, "rst\nsecond\nthird", 0),
        ("first\nsecond\nthird", 9, "first\nond\nthird", 6),
        ("first\nsecond\nthird", 18, "first\nsecond\n", 13),
        ("first\n\nthird", 6, "first\nthird", 5),
    ),
)
def test_ctrl_u_deletes_to_current_line_start(
    text: str,
    cursor_position: int,
    expected_text: str,
    expected_cursor: int,
) -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document(text, cursor_position=cursor_position)

    press_history_key(model, Keys.ControlU, buffer)

    assert buffer.text == expected_text
    assert buffer.cursor_position == expected_cursor


def test_ctrl_u_preserves_folded_paste_state_after_cursor() -> None:
    model = TuiInputModel()
    first_text = "a" * 1200
    second_text = "b" * 1200
    first = model._display_paste(first_text, "")
    second = model._display_paste(second_text, first)
    buffer = Buffer()
    buffer.document = Document(
        f"{first}\n{second}",
        cursor_position=len(first) + 1 + len(second),
    )

    press_history_key(model, Keys.ControlU, buffer)

    assert buffer.text == f"{first}\n"
    assert model.submission_state() == {first: first_text}
    assert model.restore_submission(buffer.text) == first_text


def test_ctrl_u_yank_restores_folded_paste_content() -> None:
    model = TuiInputModel()
    original = "x" * 1200
    placeholder = model._display_paste(original, "")
    buffer = Buffer()
    buffer.document = Document(placeholder, cursor_position=len(placeholder))

    press_history_key(model, Keys.ControlU, buffer)
    assert buffer.text == ""
    assert model.submission_state() == {}

    press_input_sequence(model, (Keys.ControlY,), buffer)

    assert buffer.text == placeholder
    assert model.restore_submission(buffer.text) == original


def test_yank_rekeys_folded_paste_when_placeholder_was_reused() -> None:
    model = TuiInputModel()
    first_original = "a" * 1200
    first_placeholder = model._display_paste(first_original, "")
    buffer = Buffer()
    buffer.document = Document(
        first_placeholder,
        cursor_position=len(first_placeholder),
    )

    press_history_key(model, Keys.ControlU, buffer)

    second_original = "b" * 1200
    second_placeholder = model._display_paste(second_original, buffer.text)
    assert second_placeholder == first_placeholder
    buffer.document = Document(
        second_placeholder,
        cursor_position=len(second_placeholder),
    )

    press_input_sequence(model, (Keys.ControlY,), buffer)

    assert buffer.text == f"{first_placeholder}{first_placeholder} #2"
    assert model.restore_submission(buffer.text) == (
        f"{second_original}{first_original}"
    )


def test_ctrl_u_at_line_start_kills_the_preceding_newline() -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("first\nsecond", cursor_position=6)

    press_history_key(model, Keys.ControlU, buffer)

    assert buffer.text == "firstsecond"
    assert buffer.cursor_position == 5

    press_input_sequence(model, (Keys.ControlY,), buffer)

    assert buffer.text == "first\nsecond"
    assert buffer.cursor_position == 6


@pytest.mark.parametrize("key", (Keys.Home, Keys.ControlA))
def test_editor_line_start_aliases_are_explicit(key: Keys) -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("first\nsecond", cursor_position=10)

    press_input_sequence(model, (key,), buffer)

    assert buffer.cursor_position == 6


def test_ctrl_a_repeats_to_the_previous_line_but_home_does_not() -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("one\ntwo\nthree", cursor_position=5)

    press_input_sequence(model, (Keys.ControlA,), buffer)
    assert buffer.cursor_position == 4

    press_input_sequence(model, (Keys.ControlA,), buffer)
    assert buffer.cursor_position == 0

    buffer.cursor_position = 4
    press_input_sequence(model, (Keys.Home,), buffer)
    assert buffer.cursor_position == 4


@pytest.mark.parametrize("key", (Keys.End, Keys.ControlE))
def test_editor_line_end_aliases_are_explicit(key: Keys) -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("first\nsecond", cursor_position=7)

    press_input_sequence(model, (key,), buffer)

    assert buffer.cursor_position == len("first\nsecond")


def test_ctrl_e_repeats_to_the_next_line_but_end_does_not() -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("one\ntwo\nthree", cursor_position=1)

    press_input_sequence(model, (Keys.ControlE,), buffer)
    assert buffer.cursor_position == 3

    press_input_sequence(model, (Keys.ControlE,), buffer)
    assert buffer.cursor_position == 7

    buffer.cursor_position = 3
    press_input_sequence(model, (Keys.End,), buffer)
    assert buffer.cursor_position == 3


def test_editor_word_movement_and_forward_kill_yank_share_runtime_keymap() -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("one two three", cursor_position=4)

    press_input_sequence(model, (Keys.Escape, "f"), buffer)
    assert buffer.cursor_position == 7

    press_input_sequence(model, (Keys.Escape, "b"), buffer)
    assert buffer.cursor_position == 4

    press_input_sequence(model, (Keys.Escape, "d"), buffer)
    assert buffer.text == "one  three"
    assert buffer.cursor_position == 4

    press_input_sequence(model, (Keys.ControlY,), buffer)
    assert buffer.text == "one two three"
    assert buffer.cursor_position == 7


def test_editor_line_end_kill_and_yank_preserve_following_lines() -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("first line\nsecond", cursor_position=5)

    press_input_sequence(model, (Keys.ControlK,), buffer)

    assert buffer.text == "first\nsecond"
    assert buffer.cursor_position == 5

    press_input_sequence(model, (Keys.ControlY,), buffer)

    assert buffer.text == "first line\nsecond"
    assert buffer.cursor_position == len("first line")


def test_ctrl_k_at_line_end_kills_the_following_newline() -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("first\nsecond", cursor_position=5)

    press_input_sequence(model, (Keys.ControlK,), buffer)

    assert buffer.text == "firstsecond"
    assert buffer.cursor_position == 5

    press_input_sequence(model, (Keys.ControlY,), buffer)

    assert buffer.text == "first\nsecond"
    assert buffer.cursor_position == 6


@pytest.mark.anyio
async def test_ctrl_d_deletes_forward_when_composer_is_not_empty() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        await runtime.open()
        try:
            buffer = runtime.screen.input.buffer
            buffer.document = Document("first", cursor_position=2)

            pipe_input.send_text("\x04")
            for _ in range(100):
                if buffer.text == "fist":
                    break
                await asyncio.sleep(0.01)

            assert buffer.text == "fist"
            assert buffer.cursor_position == 2
        finally:
            await runtime.close()


@pytest.mark.parametrize(
    "keys",
    (
        (Keys.ControlW,),
        (Keys.Escape, Keys.Backspace),
    ),
)
def test_editor_backward_word_kill_aliases_feed_yank(
    keys: tuple[Keys | str, ...],
) -> None:
    model = TuiInputModel()
    buffer = Buffer()
    buffer.document = Document("one two", cursor_position=len("one two"))

    press_input_sequence(model, keys, buffer)
    assert buffer.text == "one "

    press_input_sequence(model, (Keys.ControlY,), buffer)
    assert buffer.text == "one two"


def test_history_search_owns_query_and_restores_full_original_draft() -> None:
    model = TuiInputModel()
    model.history.append_submission("alpha old", {}, shell_mode=False)
    model.history.append_submission("beta", {}, shell_mode=False)
    model.history.append_submission("alpha new", {}, shell_mode=True)
    original_paste = "[Pasted Content 1200 chars]"
    model.restore_submission_state({original_paste: "x" * 1200})
    model.set_shell_mode(False)
    buffer = Buffer()
    buffer.document = Document(
        f"draft {original_paste}",
        cursor_position=3,
    )

    model.begin_history_search(buffer)

    assert buffer.text == f"draft {original_paste}"
    assert model.history_search_snapshot() is not None
    assert model.history_search_snapshot().status == "idle"

    model.append_history_search_text(buffer, "alpha")

    assert buffer.text == "alpha new"
    assert model.shell_mode
    assert model.history_search_snapshot().status == "match"

    model.step_history_search(buffer, older=True)

    assert buffer.text == "alpha old"
    assert not model.shell_mode

    assert model.cancel_history_search(buffer)
    assert buffer.document == Document(
        f"draft {original_paste}",
        cursor_position=3,
    )
    assert model.submission_state() == {original_paste: "x" * 1200}
    assert not model.shell_mode


def test_history_search_accepts_match_without_submitting() -> None:
    model = TuiInputModel()
    model.history.append_submission("matching prompt", {}, shell_mode=False)
    buffer = Buffer()
    buffer.text = "draft"

    model.begin_history_search(buffer)
    model.append_history_search_text(buffer, "match")

    assert model.accept_history_search(buffer)
    assert buffer.text == "matching prompt"
    assert buffer.cursor_position == len("matching prompt")
    assert not model.history_search_active


@pytest.mark.anyio
async def test_history_search_real_keys_accept_without_turn_submission() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        runtime.input_model.history.append_submission(
            "matching prompt",
            {},
            shell_mode=False,
        )
        await runtime.open()
        try:
            runtime.screen.input.buffer.text = "draft"
            pipe_input.send_text("\x12match")
            for _index in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.input.buffer.text == "matching prompt":
                    break

            assert runtime.input_model.history_search_active
            footer_text = fragments_text(runtime.screen._footer_fragments())
            assert "reverse-i-search: match" in footer_text
            assert "enter accept · esc cancel" in footer_text
            assert runtime.screen.input.window.always_hide_cursor()

            pipe_input.send_text("\r")
            await asyncio.sleep(0.05)

            assert not runtime.input_model.history_search_active
            assert runtime.screen.input.buffer.text == "matching prompt"
            assert runtime.submissions.message_queue.empty()
            assert not runtime.screen.input.window.always_hide_cursor()
        finally:
            await runtime.close()


def test_shell_history_does_not_restart_after_deleting_current_entry() -> None:
    model = TuiInputModel()
    model.history.append_string("! rg TODO")
    buffer = Buffer(history=model.history)

    model._navigate_history(buffer, step=-1, count=1)

    assert model.shell_mode
    assert buffer.text == "rg TODO"

    buffer.document = Document("", cursor_position=0)
    model._navigate_history(buffer, step=-1, count=1)

    assert model.shell_mode
    assert buffer.text == ""


@pytest.mark.anyio
async def test_folded_paste_history_recall_resubmits_original_text() -> None:
    runtime = TuiRuntime()
    original = "long pasted context " * 80
    placeholder = runtime.input_model._display_paste(original, "")
    buffer = runtime.screen.input.buffer
    buffer.text = placeholder

    assert await submit(runtime) == original.strip()
    assert runtime.input_model.history.get_strings() == [original.strip()]

    runtime.input_model._navigate_history(buffer, step=-1, count=1)

    assert buffer.text == original.strip()
    assert runtime.input_model.submission_state() == {}
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

    assert runtime.input_model.history.get_strings() == list(originals)

    runtime.input_model._navigate_history(buffer, step=-1, count=1)
    assert buffer.text == originals[1]

    runtime.input_model._navigate_history(buffer, step=-1, count=1)
    assert buffer.text == originals[0]


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

    assert buffer.text == expected
    assert runtime.input_model.submission_state() == {}
    assert await submit(runtime) == expected


@pytest.mark.anyio
async def test_shell_history_restores_folded_paste_for_resubmission() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    original = "shell argument " * 100
    placeholder = runtime.input_model._display_paste(original, "")
    runtime.input_model.set_shell_mode(True)
    buffer.text = placeholder

    assert await submit(runtime) == f"! {original.strip()}"
    assert runtime.input_model.history.get_strings() == [f"! {original.strip()}"]

    runtime.input_model._navigate_history(buffer, step=-1, count=1)

    assert runtime.input_model.shell_mode
    assert buffer.text == original.strip()
    assert await submit(runtime) == f"! {original.strip()}"


@pytest.mark.anyio
async def test_literal_bang_paste_stays_a_normal_query() -> None:
    runtime = TuiRuntime()
    original = "! literal pasted content " * 80
    placeholder = runtime.input_model._display_paste(original, "")
    buffer = runtime.screen.input.buffer
    buffer.text = placeholder

    assert await submit(runtime) == original.strip()
    assert not runtime.input_model.shell_mode
    assert runtime.input_model.history.get_strings() == [placeholder]
    assert runtime.document.blocks[-1].kind == "user"
    assert fragments_text(runtime.document.blocks[-1].display_block.fragments).startswith(
        "› ! literal pasted content"
    )

    runtime.input_model._navigate_history(buffer, step=-1, count=1)
    assert not runtime.input_model.shell_mode
    assert buffer.text == placeholder
    assert runtime.input_model.restore_submission(buffer.text) == original.strip()


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
