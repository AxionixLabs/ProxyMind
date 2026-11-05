# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import math
import os
import subprocess
import sys
import typing
import uuid
from collections.abc import Callable
from pathlib import Path

from agent.ports.desktop import (
    DesktopCapabilities,
    DesktopClick,
    DesktopDrag,
    DesktopError,
    DesktopInput,
    DesktopInputResult,
    DesktopKey,
    DesktopMove,
    DesktopObservation,
    DesktopPoint,
    DesktopScroll,
    DesktopText,
    DesktopWindows,
)
from agent.protocol.json_value import ThawedJsonValue
from infrastructure.sidecars.desktop.protocol import (
    RESPONSE_LIMIT,
    decode_capabilities,
    decode_input_result,
    decode_observation,
    decode_observation_validated,
    decode_response,
    decode_windows,
    encode_request,
)

ResultValue = typing.TypeVar("ResultValue")
DEFAULT_DESKTOP_REQUEST_TIMEOUT = 30.0


class DesktopSidecarSession:
    """实现单个原生桌面进程的有界传输，不拥有业务会话或用户授权。"""

    def __init__(self, process: asyncio.subprocess.Process, *, timeout: float) -> None:
        """接管由工厂创建的进程与管道，供 Harness 显式关闭。"""
        self._process = process
        self._timeout = timeout
        self._capabilities: DesktopCapabilities | None = None
        self._lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._exchange_task: asyncio.Task[bytes] | None = None
        self._stderr = bytearray()
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    @classmethod
    async def start(
        cls,
        command: tuple[str, ...],
        *,
        expected_platform: str,
        timeout: float = DEFAULT_DESKTOP_REQUEST_TIMEOUT,
    ) -> "DesktopSidecarSession":
        """启动已解析的原生命令并校验握手，失败时回收进程。"""
        if not command or not math.isfinite(timeout) or timeout <= 0 or expected_platform not in {"windows", "macos"}:
            raise ValueError("Invalid desktop launch parameters")
        try:
            process = await asyncio.create_subprocess_exec(
                *command, "--parent-pid", str(os.getpid()),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=RESPONSE_LIMIT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except OSError as error:
            raise DesktopError("unavailable", f"Cannot launch desktop sidecar: {error}") from error
        session = cls(process, timeout=timeout)
        accepted = False
        try:
            capabilities = await session._request("hello", {}, decode_capabilities)
            if capabilities.platform != expected_platform:
                raise DesktopError("protocol_error", "Desktop sidecar platform mismatch")
            session._capabilities = capabilities
            accepted = True
            return session
        finally:
            if not accepted:
                await session.close()

    @property
    def capabilities(self) -> DesktopCapabilities:
        """返回完成握手后的原生能力。"""
        if self._capabilities is None:
            raise DesktopError("unavailable", "Desktop handshake is incomplete")
        return self._capabilities

    @property
    def stderr_tail(self) -> str:
        """返回有界诊断尾部，不收集完整画面或输入正文。"""
        return self._stderr.decode("utf-8", errors="replace")

    async def list_windows(self) -> DesktopWindows:
        """通过版本化协议枚举真实窗口。"""
        return await self._request("list_windows", {}, decode_windows)

    async def activate(self, window: str) -> DesktopInputResult:
        """显式请求激活窗口，不把窗口枚举视为激活授权。"""
        self._require_input()
        return await self._request("activate", {"window": window}, decode_input_result)

    async def input(self, command: DesktopInput) -> DesktopInputResult:
        """把具名输入转换为私有协议，一次提交且不重放。"""
        self._require_input()
        operation = command.operation
        payload: dict[str, ThawedJsonValue]
        match operation:
            case DesktopClick():
                payload = {"kind": "click", "target": _target(operation.target),
                           "button": operation.button, "count": operation.count}
            case DesktopMove():
                payload = {"kind": "move", "target": _target(operation.target), "durationMs": operation.duration_ms}
            case DesktopDrag():
                payload = {"kind": "drag", "start": _target(operation.start), "end": _target(operation.end),
                           "durationMs": operation.duration_ms}
            case DesktopScroll():
                payload = {"kind": "scroll", "target": _target(operation.target),
                           "deltaX": operation.delta_x, "deltaY": operation.delta_y}
            case DesktopKey():
                payload = {"kind": "press_key", "keys": list(operation.keys)}
            case DesktopText():
                payload = {"kind": "type_text", "text": operation.text}
        return await self._request("input", {"window": command.window, "observation": command.observation,
                                             "operation": payload}, decode_input_result)

    def _require_input(self) -> None:
        """以当前进程握手的能力为准，缺失能力时不发送输入。"""
        if not self.capabilities.input_supported:
            raise DesktopError("unsupported_action", "This desktop sidecar does not support input")

    async def observe(self, window: str, *, screenshot: bool) -> DesktopObservation:
        """观察指定窗口并校验返回窗口身份与截图请求。"""
        if not window.strip() or len(window) > 64 or not isinstance(screenshot, bool):
            raise ValueError("Invalid desktop observation request")

        def decode(value: dict[str, ThawedJsonValue]) -> DesktopObservation:
            """校验本次请求的目标窗口与是否需要图片。"""
            result = decode_observation(value, window)
            if (result.image is not None) != screenshot:
                raise DesktopError("protocol_error", "Desktop screenshot result mismatch")
            if result.coordinate_space != self.capabilities.coordinate_space:
                raise DesktopError("protocol_error", "Desktop observation coordinate space mismatch")
            return result

        return await self._request("observe", {"window": window, "screenshot": screenshot}, decode)

    async def validate_observation(self, window: str, observation: str) -> None:
        """由原生会话校验观察有效性，失效时要求重新观察，不自动刷新或重放。"""
        if not window.strip() or len(window) > 64 or not observation.strip() or len(observation) > 64:
            raise ValueError("Invalid desktop observation reference")
        await self._request("validate_observation", {"window": window, "observation": observation},
                            decode_observation_validated)

    async def _request(
        self,
        action: str,
        parameters: dict[str, ThawedJsonValue],
        decode: Callable[[dict[str, ThawedJsonValue]], ResultValue],
    ) -> ResultValue:
        """串行交换单帧，取消与传输失败销毁会话且不重放动作。"""
        request_id = uuid.uuid4().hex
        payload = encode_request(request_id, action, parameters)
        async with self._lock:
            if self._close_task is not None:
                raise DesktopError("unavailable", "Desktop session is closed")
            exchange = asyncio.create_task(self._exchange(payload))
            self._exchange_task = exchange
            try:
                frame = await exchange
                return decode(decode_response(frame, request_id))
            except TimeoutError as error:
                await self.close()
                raise DesktopError("timeout", "Desktop request timed out; session closed, no replay") from error
            except asyncio.CancelledError:
                await self.close()
                raise
            except (OSError, ValueError) as error:
                await self.close()
                raise DesktopError("protocol_error", "Desktop transport failed; session closed, no replay") from error
            except DesktopError as error:
                if error.code in {"protocol_error", "transport_closed"}:
                    await self.close()
                raise
            finally:
                self._exchange_task = None

    async def _exchange(self, payload: bytes) -> bytes:
        """持有唯一管道读写任务，关闭方可取消它而不与请求争抢 stdout。"""
        stdin, stdout = self._process.stdin, self._process.stdout
        if stdin is None or stdout is None:
            raise DesktopError("transport_closed", "Desktop pipes are missing")
        async with asyncio.timeout(self._timeout):
            stdin.write(payload)
            await stdin.drain()
            frame = await stdout.readline()
            if not frame:
                raise DesktopError("transport_closed", "Desktop exited before returning a response; no replay")
            return frame

    async def _drain_stderr(self) -> None:
        """持续排空原生 stderr，仅保留最近四 KiB。"""
        stderr = self._process.stderr
        if stderr is None:
            return
        while chunk := await stderr.read(4096):
            self._stderr.extend(chunk)
            del self._stderr[:-4096]

    async def close(self) -> None:
        """共享唯一关闭任务，调用方取消等待不会中断进程回收。"""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_process())
        await asyncio.shield(self._close_task)

    async def _close_process(self) -> None:
        """结束活动读写，再发送 EOF 并排空双管道，超时后终止进程。"""
        exchange = self._exchange_task
        if exchange is not None:
            exchange.cancel()
            # 请求任务负责交付结果或异常；关闭方只等待管道读写停止。
            await asyncio.gather(exchange, return_exceptions=True)
        drain_stdout = asyncio.create_task(self._discard_stdout())
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except TimeoutError:
                try:
                    if self._process.returncode is None:
                        self._process.kill()
                except ProcessLookupError:
                    pass
                await self._process.wait()
        finally:
            await asyncio.gather(drain_stdout, self._stderr_task)

    async def _discard_stdout(self) -> None:
        """关闭期间丢弃剩余输出，避免满管道阻塞退出或保留大响应。"""
        stdout = self._process.stdout
        if stdout is not None:
            while await stdout.read(64 * 1024):
                pass


def _target(value: DesktopPoint | int) -> dict[str, ThawedJsonValue]:
    """保留控件与截图坐标两种互斥目标的协议形状。"""
    if isinstance(value, DesktopPoint):
        return {"x": value.x, "y": value.y}
    return {"element": value}


async def create_desktop_session(executable: Path, *, platform: str | None = None) -> DesktopSidecarSession:
    """隔离平台选择，要求组合根提供对应平台的已发布原生资产。"""
    selected = sys.platform if platform is None else platform
    expected = {"win32": "windows", "darwin": "macos"}.get(selected)
    if expected is None:
        raise DesktopError("unsupported_platform", f"Desktop automation is unavailable on {selected}")
    if not executable.is_file():
        raise DesktopError("unavailable", f"Desktop sidecar executable is missing: {executable}")
    return await DesktopSidecarSession.start((str(executable.resolve()),), expected_platform=expected)


if __name__ == '__main__':
    pass
