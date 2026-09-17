# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.keys import Keys

from infrastructure.platform.hidden_input import _BoundedInputParser


@pytest.mark.parametrize("sequence", ["\x1b[200~" + "私" * 40, "\x1b[" + "1" * 257])
def test_incomplete_terminal_sequences_are_bounded_before_key_delivery(sequence):
    keys = []
    parser = _BoundedInputParser(keys.append, 80)
    with pytest.raises(BufferError):
        for offset in range(0, len(sequence), 7):
            parser.feed(sequence[offset:offset + 7])
    assert keys == []


def test_complete_paste_preserves_exact_byte_limit_and_following_keys():
    keys = []
    parser = _BoundedInputParser(keys.append, 81)
    content = "私" * 27
    for character in f"\x1b[200~{content}\x1b[201~\r":
        parser.feed(character)
    assert [(key.key, key.data) for key in keys] == [(Keys.BracketedPaste, content), (Keys.ControlM, "\r")]


@pytest.mark.parametrize("control,key", [("\x03", Keys.ControlC), ("\x04", Keys.ControlD), ("\x1a", Keys.ControlZ)])
def test_unterminated_paste_still_accepts_cancel_and_eof(control, key):
    keys = []
    parser = _BoundedInputParser(keys.append, 80)
    parser.feed("\x1b[200~private" + control)
    assert [press.key for press in keys] == [key]
    assert parser._paste_buffer == ""
