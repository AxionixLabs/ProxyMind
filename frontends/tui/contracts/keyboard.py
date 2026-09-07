# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from prompt_toolkit.output.base import Output

from frontends.terminal.identity import TerminalIdentity


_ENHANCED_KEY_BASE: typing.Final[int] = 0xF0000

_MODIFIER_BITS: typing.Final[typing.Mapping[str, int]] = {
    "ctrl": 1,
    "alt": 2,
    "shift": 4,
}

_NAMED_KEYS: typing.Final[tuple[str, ...]] = (
    "esc",
    "enter",
    "tab",
    "space",
    "backspace",
    "delete",
    "insert",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "page-up",
    "page-down",
    *(f"f{number}" for number in range(1, 25)),
)
_NAMED_KEY_IDS: typing.Final[typing.Mapping[str, int]] = {
    name: 128 + index
    for index, name in enumerate(_NAMED_KEYS)
}

_MAX_TOKEN_OFFSET: typing.Final[int] = (
    (127 + len(_NAMED_KEYS)) * 8 + 7
)


@typing.runtime_checkable
class TerminalKeyboardBindable(typing.Protocol):
    """定义输入 adapter 接收终端生命周期依赖的组合边界。

    实现方拥有增强键盘模式和平台 job control，调用方只在 Application
    完成输入输出装配后绑定一次，不读取具体平台状态。挂起准备只负责临时
    离开前端专用终端表面，恢复回调必须按准备结果幂等还原该表面。
    """

    def bind_terminal_output(
        self,
        output: Output,
        identity: TerminalIdentity,
    ) -> None:
        """绑定与当前输入共享生命周期的终端输出。"""
        ...

    def bind_terminal_suspend_lifecycle(
        self,
        prepare: typing.Callable[[], bool],
        restore: typing.Callable[[bool], None],
    ) -> None:
        """绑定平台挂起前后的前端终端表面生命周期。"""
        ...


def enhanced_key_token(
    key_name: str,
    modifiers: typing.AbstractSet[str],
) -> str | None:
    """把可配置逻辑修饰键映射为 Prompt Toolkit 可绑定的内部令牌。"""
    modifier_mask = 0
    for modifier in modifiers:
        bit = _MODIFIER_BITS.get(modifier)
        if bit is None:
            return None
        modifier_mask |= bit
    if modifier_mask == 0:
        return None

    if len(key_name) == 1 and ord(key_name) < 128:
        key_id = ord(key_name)
    else:
        key_id = _NAMED_KEY_IDS.get(key_name, -1)
    if key_id < 0:
        return None
    return chr(_ENHANCED_KEY_BASE + key_id * 8 + modifier_mask)


def is_enhanced_key_token(value: str) -> bool:
    """判断字符是否属于内部增强按键令牌区间。"""
    if len(value) != 1:
        return False
    offset = ord(value) - _ENHANCED_KEY_BASE
    return 0 < offset <= _MAX_TOKEN_OFFSET and offset % 8 != 0


if __name__ == '__main__':
    pass
