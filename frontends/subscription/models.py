# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


@dataclass(slots=True)
class AgentConfig:
    """订阅模式配置：统一收敛命令行和运行时默认值。"""
    base_url: str
    device_id: str
    agent_id: str
    client_version: str
    platform: str
    arch: str


@dataclass
class AgentSessionRuntime:
    """订阅会话运行态：保存断线恢复所需的动态状态。"""
    session_id: str
    ws_token: str
    resume_token: str | None
    credential: str | None
    mind_call_example: dict[str, typing.Any] | None
    ws_url: str | None
    device_id: str
    client_version: str
    last_acked_seq: int = 0
    ready_received: bool = False
    pre_ready_connect_failures: int = 0
    forwarded_message_ids: set[str] | None = None


@dataclass(slots=True)
class AgentForwardRequest:
    """服务端下发的本地执行请求。"""
    message_id: str
    call_id: str
    cid: str
    sid: str
    payload: dict[str, typing.Any]


class AgentInboxItem:
    """本地收件箱中的服务端请求。"""
    request: AgentForwardRequest
    status: typing.Literal["pending", "running", "completed", "failed"]
    error: str | None

    __slots__ = ("request", "status", "error")

    def __init__(
        self,
        request: AgentForwardRequest,
        status: typing.Literal["pending", "running", "completed", "failed"] = "pending",
        error: str | None = None
    ) -> None:
        """初始化收件箱请求条目。"""
        self.request = request
        self.status  = status
        self.error   = error

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AgentInboxItem):
            return NotImplemented
        return (
            self.request == other.request
            and self.status == other.status
            and self.error == other.error
        )

    def __repr__(self) -> str:
        return (
            f"AgentInboxItem("
            f"request={self.request!r}, "
            f"status={self.status!r}, "
            f"error={self.error!r})"
        )


@dataclass
class AgentLiveStatus:
    """保存订阅生命周期的当前状态摘要。"""
    title: str = "Listener Idle"
    detail: str = "Not connected"

    def snapshot(self) -> tuple[str, str]:
        return self.title, self.detail

    def update(self, title: str, detail: str) -> None:
        self.title = title
        self.detail = detail


if __name__ == '__main__':
    pass
