import contextlib
import typing

from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys

from frontends.tui.adapters.input import WindowsUnicodeInputAdapter


class _BatchInput(Input):
    """按调用顺序返回固定按键批次。"""

    def __init__(self, batches: tuple[tuple[KeyPress, ...], ...]) -> None:
        self._batches = list(batches)
        self.closed_value = False

    @property
    def closed(self) -> bool:
        """返回测试输入是否关闭。"""
        return self.closed_value

    def fileno(self) -> int:
        """返回稳定测试描述符。"""
        return 7

    def typeahead_hash(self) -> str:
        """返回稳定测试身份。"""
        return "batch-input"

    def read_keys(self) -> list[KeyPress]:
        """读取下一批按键。"""
        if not self._batches:
            return []
        return list(self._batches.pop(0))

    def raw_mode(self) -> typing.ContextManager[None]:
        """返回空 raw mode 上下文。"""
        return contextlib.nullcontext()

    def cooked_mode(self) -> typing.ContextManager[None]:
        """返回空 cooked mode 上下文。"""
        return contextlib.nullcontext()

    def attach(
        self,
        _input_ready_callback: typing.Callable[[], None],
    ) -> typing.ContextManager[None]:
        """返回空事件循环挂接上下文。"""
        return contextlib.nullcontext()

    def detach(self) -> typing.ContextManager[None]:
        """返回空事件循环解除挂接上下文。"""
        return contextlib.nullcontext()


def test_windows_unicode_input_merges_surrogates_across_reads() -> None:
    """验证非 BMP 字符跨底层读取批次仍形成一个按键。"""
    source = _BatchInput((
        (KeyPress("\ud83d", "\ud83d"),),
        (
            KeyPress("\ude42", "\ude42"),
            KeyPress("\u0301", "\u0301"),
        ),
    ))
    input_obj = WindowsUnicodeInputAdapter(source)

    assert input_obj.read_keys() == []
    assert input_obj.read_keys() == [
        KeyPress("🙂", "🙂"),
        KeyPress("\u0301", "\u0301"),
    ]


def test_windows_unicode_input_replaces_unpaired_surrogates_on_flush() -> None:
    """验证 flush 不向编辑器泄漏孤立 UTF-16 码元。"""
    source = _BatchInput(((KeyPress("\ud83d", "\ud83d"),),))
    input_obj = WindowsUnicodeInputAdapter(source)

    assert input_obj.read_keys() == []
    assert input_obj.flush_keys() == [KeyPress("\ufffd", "\ufffd")]


def test_windows_unicode_input_normalizes_bracketed_paste_payload() -> None:
    """验证粘贴载荷中的合法 surrogate 对和孤立码元同时收敛。"""
    source = _BatchInput(((KeyPress(
        Keys.BracketedPaste,
        "paste-\ud83d\ude42-\ud83d",
    ),),))
    input_obj = WindowsUnicodeInputAdapter(source)

    assert input_obj.read_keys() == [KeyPress(
        Keys.BracketedPaste,
        "paste-🙂-\ufffd",
    )]
