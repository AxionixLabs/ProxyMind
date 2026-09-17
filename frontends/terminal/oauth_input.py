# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys

from agent.domain.mcp_oauth import McpOAuthError
from infrastructure.platform.hidden_input import (
    create_hidden_input,
    hidden_terminal_mode,
)


class _CallbackLine:
    """拥有一次输入的有界字符缓冲，不提供历史、回显或内容诊断。"""

    def __init__(self, max_bytes: int) -> None:
        """冻结字节上限，编辑操作始终更新实际 UTF-8 大小。"""
        self._limit = max_bytes
        self._parts: list[str] = []
        self._size = 0

    def accept(self, press: KeyPress) -> str | None:
        """处理提交、取消、清空、退格和粘贴，其他控制键不进入 URL。"""
        if press.key == Keys.ControlC:
            raise asyncio.CancelledError
        if press.key in (Keys.ControlD, Keys.ControlZ):
            raise McpOAuthError("callback_input_closed")
        if press.key in (Keys.ControlM, Keys.ControlJ):
            if not self._parts:
                raise McpOAuthError("callback_input_closed")
            return "".join(self._parts)
        if press.key == Keys.ControlU:
            self._parts.clear()
            self._size = 0
        elif press.key in (Keys.Backspace, Keys.ControlH):
            if self._parts:
                part = self._parts.pop()
                self._size -= len(part[-1].encode("utf-8"))
                if len(part) > 1:
                    self._parts.append(part[:-1])
        elif press.key == Keys.BracketedPaste or not isinstance(press.key, Keys):
            data = press.data
            if len(data) > self._limit - self._size:
                raise McpOAuthError("callback_input_too_long")
            size = len(data.encode("utf-8"))
            if self._size + size > self._limit:
                raise McpOAuthError("callback_input_too_long")
            if data:
                self._parts.append(data)
                self._size += size
        return None


class HiddenOAuthCallbackInput:
    """实现单次 CLI 隐藏回调输入，平台 adapter 管理模式，prompt_toolkit 提供可取消监听。

    每次读取独占创建输入 adapter；退出先注销监听、恢复模式，再关闭 adapter。
    不创建阻塞 stdin 读取线程，不保留历史，也不把粘贴内容交给展示层。
    """

    def __init__(
        self, stdin: typing.TextIO, output: typing.TextIO,
        *, input_factory: typing.Callable[[], Input] | None = None,
    ) -> None:
        """要求交互终端，避免将敏感回调退回普通回显输入。"""
        if not stdin.isatty():
            raise McpOAuthError("callback_input_unavailable")
        self._stdin = stdin
        self._output = output
        self._input_factory = input_factory

    async def read_callback(self, *, max_bytes: int) -> str:
        """在可取消输入范围内隐藏读取，所有终态都会恢复终端并关闭监听。"""
        terminal: Input | None = None
        result: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        line = _CallbackLine(max_bytes)
        try:
            terminal = self._input_factory() if self._input_factory is not None else create_hidden_input(self._stdin, max_bytes=max_bytes)
            input_source = terminal

            def ready() -> None:
                """只归约一次读取的按键，将固定失败交给等待任务并避免回调异常泄漏。"""
                if result.done():
                    return
                try:
                    for press in input_source.read_keys():
                        value = line.accept(press)
                        if value is not None:
                            result.set_result(value)
                            return
                    if input_source.closed:
                        raise McpOAuthError("callback_input_closed")
                except asyncio.CancelledError:
                    result.cancel()
                except McpOAuthError as error:
                    result.set_exception(error)
                except BufferError:
                    result.set_exception(McpOAuthError("callback_input_too_long"))
                except (OSError, EOFError, UnicodeError):
                    result.set_exception(McpOAuthError("callback_input_unavailable"))

            with hidden_terminal_mode(self._stdin), terminal.raw_mode(), terminal.attach(ready):
                self._output.write(
                    "After signing in, copy the full URL from the browser's address bar.\n"
                    "If the callback page cannot load, paste that URL here anyway.\n"
                    "Callback URL (input hidden; Enter submits, Ctrl+C cancels): "
                )
                self._output.flush()
                ready()
                return await result
        except McpOAuthError:
            raise
        except (OSError, EOFError, ValueError, RuntimeError):
            raise McpOAuthError("callback_input_unavailable") from None
        finally:
            try:
                if terminal is not None:
                    terminal.close()
            except (OSError, EOFError, ValueError, RuntimeError):
                raise McpOAuthError("callback_input_unavailable") from None
            finally:
                if result.done() and not result.cancelled():
                    result.exception()
                else:
                    result.cancel()
                self._output.write("\n")
                self._output.flush()


if __name__ == '__main__':
    pass
