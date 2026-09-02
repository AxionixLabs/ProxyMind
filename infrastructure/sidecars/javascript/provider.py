# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import asyncio
from pathlib import Path

from agent.ports.capabilities import SandboxMode
from agent.ports.javascript import (
    JavaScriptExecution,
    JavaScriptExecutionError,
    JavaScriptFailureKind,
    NestedToolDispatch,
)
from infrastructure.sidecars.javascript.bundle import JavaScriptBundle
from infrastructure.sidecars.javascript.process import resolve_node_path
from infrastructure.sidecars.javascript.session import JavaScriptSidecarSession


class JavaScriptSidecarProvider:
    """按 Session 和安全信封提供惰性 JavaScript Sidecar。"""

    def __init__(
        self,
        root: str | Path,
        *,
        asset_root: str | Path | None = None,
        configured_node_path: str | None = None,
    ) -> None:
        """固定默认工作区、不可变 bundle 和 Node 配置候选。"""
        self.root = Path(root).resolve()
        default_asset_root = (
            Path(__file__).resolve().parents[3] / "sidecars" / "js_repl"
        )
        self.bundle = JavaScriptBundle.at(asset_root or default_asset_root)
        self.configured_node_path = configured_node_path
        self._sessions: dict[str, JavaScriptSidecarSession] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def ensure_available(self) -> None:
        """不创建 Session 地验证 bundle 和 Node 运行时。"""
        self.bundle.verify()
        await resolve_node_path(self.configured_node_path)

    async def execute(
        self,
        session_id: str,
        code: str,
        *,
        cwd: str | Path | None,
        timeout_ms: int,
        call_tool: NestedToolDispatch,
        access_mode: str = "workspace-write",
    ) -> JavaScriptExecution:
        """在匹配 Session 安全信封的 Kernel 中执行一个 Cell。"""
        if self._closed:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                "JavaScript sidecar provider is closed",
            )
        key = str(session_id or "").strip()
        if not key:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.RUNTIME_ERROR,
                "js_repl session id is required",
            )
        sandbox_mode = _sandbox_mode(access_mode)
        target_cwd = Path(cwd or self.root).resolve()

        async with self._lock:
            session = self._sessions.get(key)
            if session is not None and (
                session.cwd != target_cwd
                or session.access_mode != sandbox_mode
            ):
                del self._sessions[key]
                await session.close()
                session = None
            if session is None:
                session = JavaScriptSidecarSession(
                    cwd=target_cwd,
                    session_id=key,
                    access_mode=sandbox_mode,
                    bundle=self.bundle,
                    configured_node_path=self.configured_node_path,
                )
                self._sessions[key] = session

        return await session.execute(
            code,
            timeout_ms=timeout_ms,
            call_tool=call_tool,
        )

    async def reset_session(self, session_id: str) -> bool:
        """重置已创建 Session；未创建时保持无副作用。"""
        key = str(session_id or "").strip()
        if not key:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.RUNTIME_ERROR,
                "js_repl session id is required",
            )
        async with self._lock:
            session = self._sessions.get(key)
        if session is None:
            return False
        await session.reset()
        return True

    async def close_session(self, session_id: str) -> bool:
        """关闭并移除指定 Session。"""
        key = str(session_id or "").strip()
        if not key:
            return False
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session is None:
            return False
        await session.close()
        return True

    async def close(self) -> None:
        """幂等关闭 Provider 持有的全部 Session。"""
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        if sessions:
            await asyncio.gather(
                *(session.close() for session in sessions),
                return_exceptions=True,
            )


def _sandbox_mode(value: str) -> SandboxMode:
    """把外部模式字符串收窄为受支持的 Sandbox 模式。"""
    if value == "read-only":
        return "read-only"
    if value == "workspace-write":
        return "workspace-write"
    if value == "danger-full-access":
        return "danger-full-access"
    raise JavaScriptExecutionError(
        JavaScriptFailureKind.RUNTIME_ERROR,
        f"unsupported js_repl access mode: {value}",
    )


if __name__ == "__main__":
    pass
