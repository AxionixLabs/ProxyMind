# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.ports.agent_messages import (
    AgentMessageDeliveryStatus,
    AgentMessageReceipt,
)

__all__ = ("AgentMessageDispatch", "AgentMessageEvent")


class AgentMessageEvent(typing.Protocol):
    """定义消息派发结果所需的最小邮箱事件形状。"""

    event_id: str
    recipient_agent_id: str
    recipient_task_path: str

    def to_dict(self) -> dict[str, typing.Any]:
        """返回事件的稳定 JSON 对象。"""
        ...


@dataclass(frozen=True, slots=True)
class AgentMessageDispatch:
    """描述邮箱消息及其实际投递通道的 application 结果。"""

    event: AgentMessageEvent
    delivery: AgentMessageDeliveryStatus
    receipt: AgentMessageReceipt | None = None

    def __post_init__(self) -> None:
        """校验投递通道与远程回执的一致性。"""
        if self.delivery not in {"active_turn", "mailbox"}:
            raise ValueError("agent message delivery status is invalid")
        if self.delivery == "active_turn":
            if self.receipt is None:
                raise ValueError("active turn delivery requires a receipt")
            if self.receipt.client_message_id != self.event.event_id:
                raise ValueError("agent message receipt does not match event")
        elif self.receipt is not None:
            raise ValueError("mailbox delivery cannot include a receipt")


if __name__ == '__main__':
    pass
