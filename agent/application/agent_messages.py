# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from agent.ports.agent_messages import (
    AgentMessageDeliveryStatus,
    AgentMessageReceipt,
)

if typing.TYPE_CHECKING:
    from agent.stores.agent_mailbox import AgentMailboxEvent

__all__ = ("AgentMessageDispatch",)


@dataclass(frozen=True, slots=True)
class AgentMessageDispatch:
    """描述邮箱消息及其实际投递通道的 application 结果。"""

    event: "AgentMailboxEvent"
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




