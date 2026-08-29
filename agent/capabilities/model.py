# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
from mind_nova.requests.chat import (
    TurnEventStream,
    stream_chat
)
from agent.ports import (
    ApprovalSnapshotCallback,
    ModelCapabilityError,
    ModelEventStream,
    ReconnectStatusCallback,
)
from agent.protocol import ModelEvent, ModelStreamRequest


class RemoteModelEventStream:
    """把旧远端事件流适配为模型能力端口并归一化迭代失败。"""

    def __init__(self, stream: TurnEventStream) -> None:
        """绑定旧传输流并初始化幂等关闭状态。"""
        self._stream = stream
        self._closed = False

    @property
    def end_reason(self) -> typing.Any:
        """返回旧传输记录的结束原因。"""
        return getattr(self._stream, "end_reason", None)

    @property
    def last_event_seq(self) -> int:
        """返回旧传输确认的最新事件序号。"""
        return int(getattr(self._stream, "last_event_seq", 0))

    def __aiter__(self) -> typing.AsyncIterator[ModelEvent]:
        """返回捕获并归一化传输异常的异步事件迭代器。"""
        return self._iterate()

    async def _iterate(self) -> typing.AsyncIterator[ModelEvent]:
        """转发事件并确保迭代结束时关闭远端资源。"""
        try:
            async for event in self._stream:
                yield _require_model_event(event)
        except asyncio.CancelledError:
            raise
        except ModelCapabilityError:
            raise
        except Exception as error:
            raise _classify_model_error(error) from error
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """幂等关闭旧传输并将关闭失败归一化。"""
        if self._closed:
            return
        self._closed = True
        try:
            await self._stream.aclose()
        except asyncio.CancelledError:
            raise
        except ModelCapabilityError:
            raise
        except Exception as error:
            raise _classify_model_error(error) from error


class RemoteModelCapability:
    """把冻结的模型请求翻译到现有远端事件流传输。"""

    def stream(
        self,
        request: ModelStreamRequest,
        *,
        on_reconnect_status: ReconnectStatusCallback | None = None,
        on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    ) -> ModelEventStream:
        """创建保留重连、审批恢复和事件水位语义的远端流。"""
        try:
            stream = stream_chat(
                request.pref_config_value(),
                request.message,
                request.tool_values(),
                attachments=request.attachment_values() or None,
                timeout=request.timeout,
                on_reconnect_status=on_reconnect_status,
                on_approval_snapshot=on_approval_snapshot,
                initial_event_seq=request.initial_event_seq,
                **request.option_values(),
            )
        except asyncio.CancelledError:
            raise
        except ModelCapabilityError:
            raise
        except Exception as error:
            raise _classify_model_error(error) from error
        return RemoteModelEventStream(stream)


def _require_model_event(event: typing.Any) -> ModelEvent:
    """校验传输对象满足 capability 对外承诺的模型事件坐标契约。"""
    if not isinstance(event, ModelEvent):
        raise TypeError("model transport returned an invalid event object")
    return event


def _classify_model_error(error: BaseException) -> ModelCapabilityError:
    """将旧传输异常映射为不依赖客户端库的稳定能力错误。"""
    if isinstance(error, ModelCapabilityError):
        return error
    details: dict[str, typing.Any] = {
        "exception_type": type(error).__name__,
    }
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code if error.response else None
        if status_code is not None:
            details["status_code"] = status_code
        return ModelCapabilityError(
            "model_transport_http_error",
            str(error).strip() or f"HTTP {status_code or 0}",
            retryable=bool(status_code is not None and (
                status_code >= 500 or status_code in {408, 425, 429}
            )),
            details=details,
        )
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return ModelCapabilityError(
            "model_transport_timeout",
            str(error).strip() or "model transport timed out",
            retryable=True,
            details=details,
        )
    if isinstance(error, (httpx.HTTPError, OSError)):
        return ModelCapabilityError(
            "model_transport_error",
            str(error).strip() or "model transport failed",
            retryable=True,
            details=details,
        )
    if isinstance(error, (TypeError, ValueError)):
        return ModelCapabilityError(
            "model_protocol_error",
            str(error).strip() or "model protocol is invalid",
            details=details,
        )
    return ModelCapabilityError(
        "model_capability_error",
        str(error).strip() or "model capability failed",
        details=details,
    )


if __name__ == '__main__':
    pass
