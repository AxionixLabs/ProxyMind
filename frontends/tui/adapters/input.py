# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing

from prompt_toolkit.input import create_input
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys


class FocusEventInputAdapter(Input):
    """消费 POSIX VT focus 事件并保留相邻用户按键。

    prompt_toolkit 3.x 会把 Focus In/Out 拆成 Escape、方括号和字母三个
    普通按键。本 adapter 只拥有跨批次 focus 前缀状态；文件描述符、终端
    模式和事件循环生命周期仍由被包装 Input 负责。
    """

    def __init__(self, input_obj: Input) -> None:
        self._input = input_obj
        self._pending: list[KeyPress] = []

    @property
    def closed(self) -> bool:
        """返回被包装输入是否已关闭。"""
        return self._input.closed

    def fileno(self) -> int:
        """返回被包装输入的文件描述符。"""
        return self._input.fileno()

    def typeahead_hash(self) -> str:
        """复用被包装输入的 typeahead 身份。"""
        return self._input.typeahead_hash()

    def read_keys(self) -> list[KeyPress]:
        """读取按键并移除完整的 Focus In/Out 事件。"""
        return self._filter_focus_events(self._input.read_keys(), final=False)

    def flush_keys(self) -> list[KeyPress]:
        """冲刷底层解析器并交付未组成 focus 事件的按键。"""
        return self._filter_focus_events(self._input.flush_keys(), final=True)

    def flush(self) -> None:
        """把事件循环 flush 请求委托给底层输入。"""
        self._input.flush()

    def raw_mode(self) -> typing.ContextManager[None]:
        """复用底层输入的 raw mode 生命周期。"""
        return self._input.raw_mode()

    def cooked_mode(self) -> typing.ContextManager[None]:
        """复用底层输入的 cooked mode 生命周期。"""
        return self._input.cooked_mode()

    def attach(
        self,
        input_ready_callback: typing.Callable[[], None],
    ) -> typing.ContextManager[None]:
        """把事件循环输入回调挂接到底层输入。"""
        return self._input.attach(input_ready_callback)

    def detach(self) -> typing.ContextManager[None]:
        """复用底层输入的事件循环解除挂接边界。"""
        return self._input.detach()

    def close(self) -> None:
        """清除前缀状态并关闭底层输入。"""
        self._pending.clear()
        self._input.close()

    def _filter_focus_events(
        self,
        keys: typing.Iterable[KeyPress],
        *,
        final: bool,
    ) -> list[KeyPress]:
        """按输入顺序消费 focus 序列并保留其他按键。"""
        pending = [*self._pending, *keys]
        self._pending.clear()
        filtered: list[KeyPress] = []
        while pending:
            if not self._is_escape(pending[0]):
                filtered.append(pending.pop(0))
                continue
            if len(pending) == 1:
                break
            if not self._is_text_key(pending[1], "["):
                filtered.append(pending.pop(0))
                continue
            if len(pending) == 2:
                break
            if self._is_text_key(pending[2], "I") or self._is_text_key(
                pending[2],
                "O",
            ):
                del pending[:3]
                continue
            filtered.append(pending.pop(0))

        if final:
            filtered.extend(pending)
        else:
            self._pending.extend(pending)
        return filtered

    @staticmethod
    def _is_escape(key: KeyPress) -> bool:
        """判断按键是否为 focus 序列起始 Escape。"""
        return key.key is Keys.Escape and key.data == "\x1b"

    @staticmethod
    def _is_text_key(key: KeyPress, expected: str) -> bool:
        """判断按键是否为指定的单个文本字符。"""
        return key.key == expected and key.data == expected


class WindowsUnicodeInputAdapter(Input):
    """在 Windows Console 读取批次之间保持完整 Unicode 输入。

    prompt_toolkit 的 VT Console reader 可能把一个非 BMP 字符的 UTF-16
    surrogate 对分到两次 `read_keys`。本 adapter 只拥有跨批合并状态，终端
    句柄、事件循环挂接和模式恢复仍由被包装 Input 负责。
    """

    def __init__(self, input_obj: Input) -> None:
        self._input = input_obj
        self._pending_high_surrogate: KeyPress | None = None

    @property
    def closed(self) -> bool:
        """返回被包装输入是否已关闭。"""
        return self._input.closed

    @property
    def handle(self) -> int | None:
        """返回 Windows Console handle 的整数值。"""
        handle = getattr(self._input, "handle", None)
        value = getattr(handle, "value", handle)
        return value if isinstance(value, int) else None

    def fileno(self) -> int:
        """返回被包装输入的文件描述符。"""
        return self._input.fileno()

    def typeahead_hash(self) -> str:
        """复用被包装输入的 typeahead 身份。"""
        return self._input.typeahead_hash()

    def read_keys(self) -> list[KeyPress]:
        """读取按键并跨读取批次合并 UTF-16 surrogate 对。"""
        return self._merge_surrogates(self._input.read_keys(), final=False)

    def flush_keys(self) -> list[KeyPress]:
        """冲刷底层解析器并收束残留的非法 surrogate。"""
        return self._merge_surrogates(self._input.flush_keys(), final=True)

    def flush(self) -> None:
        """把事件循环 flush 请求委托给底层输入。"""
        self._input.flush()

    def raw_mode(self) -> typing.ContextManager[None]:
        """复用底层输入的 raw mode 生命周期。"""
        return self._input.raw_mode()

    def cooked_mode(self) -> typing.ContextManager[None]:
        """复用底层输入的 cooked mode 生命周期。"""
        return self._input.cooked_mode()

    def attach(
        self,
        input_ready_callback: typing.Callable[[], None],
    ) -> typing.ContextManager[None]:
        """把事件循环输入回调挂接到底层输入。"""
        return self._input.attach(input_ready_callback)

    def detach(self) -> typing.ContextManager[None]:
        """复用底层输入的事件循环解除挂接边界。"""
        return self._input.detach()

    def close(self) -> None:
        """清除合并状态并关闭底层输入。"""
        self._pending_high_surrogate = None
        self._input.close()

    def _merge_surrogates(
        self,
        keys: typing.Iterable[KeyPress],
        *,
        final: bool,
    ) -> list[KeyPress]:
        """按输入顺序合并成对 surrogate 并替换孤立码元。"""
        merged: list[KeyPress] = []
        for key in keys:
            value = key.key
            if value is Keys.BracketedPaste:
                merged.append(KeyPress(
                    Keys.BracketedPaste,
                    self._normalize_utf16_text(key.data),
                ))
                continue
            pending = self._pending_high_surrogate
            if pending is not None:
                if self._is_low_surrogate(value):
                    merged.append(self._paired_key(pending, key))
                    self._pending_high_surrogate = None
                    continue
                merged.append(KeyPress("\ufffd", "\ufffd"))
                self._pending_high_surrogate = None

            if self._is_high_surrogate(value):
                self._pending_high_surrogate = key
            elif self._is_low_surrogate(value):
                merged.append(KeyPress("\ufffd", "\ufffd"))
            else:
                merged.append(key)

        if final and self._pending_high_surrogate is not None:
            merged.append(KeyPress("\ufffd", "\ufffd"))
            self._pending_high_surrogate = None
        return merged

    @staticmethod
    def _is_high_surrogate(value: Keys | str) -> bool:
        """判断按键值是否为 UTF-16 高 surrogate。"""
        return bool(
            not isinstance(value, Keys)
            and "\ud800" <= value <= "\udbff"
        )

    @staticmethod
    def _is_low_surrogate(value: Keys | str) -> bool:
        """判断按键值是否为 UTF-16 低 surrogate。"""
        return bool(
            not isinstance(value, Keys)
            and "\udc00" <= value <= "\udfff"
        )

    @staticmethod
    def _paired_key(high: KeyPress, low: KeyPress) -> KeyPress:
        """把完整 UTF-16 surrogate 对转换为单个 Unicode 按键。"""
        text = (
            f"{high.key}{low.key}"
            .encode("utf-16-le", errors="surrogatepass")
            .decode("utf-16-le")
        )
        return KeyPress(text, text)

    @staticmethod
    def _normalize_utf16_text(value: str) -> str:
        """合并载荷内合法 surrogate 对并替换孤立码元。"""
        return (
            value.encode("utf-16-le", errors="surrogatepass")
            .decode("utf-16-le", errors="replace")
        )


def create_tui_input(stream: typing.TextIO) -> Input:
    """按当前平台创建并规范化 prompt_toolkit 输入。"""
    if sys.platform == "win32":
        from frontends.tui.adapters.windows_input import create_windows_input

        return WindowsUnicodeInputAdapter(create_windows_input(stream))
    return FocusEventInputAdapter(create_input(stream))


if __name__ == '__main__':
    pass
