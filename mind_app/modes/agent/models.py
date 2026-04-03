# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
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
    access_token: str | None
    mind_call_examples: list[dict[str, typing.Any]] | None
    ws_url: str | None
    device_id: str
    client_version: str
    last_acked_seq: int = 0
    forwarded_message_ids: set[str] | None = None
    pending_tasks: set[asyncio.Task[None]] | None = None


@dataclass
class AgentLiveStatus:
    """订阅模式等待动画的共享状态。"""
    title: str = "Entering Fold Mode"
    detail: str = "Preparing subscription link"

    def snapshot(self) -> tuple[str, str]:
        return self.title, self.detail

    def update(self, title: str, detail: str) -> None:
        self.title = title
        self.detail = detail


if __name__ == '__main__':
    pass
