# -*- coding: utf-8 -*-

import typing
from dataclasses import dataclass
from protocol.schema.turn_inputs import TurnInput

AgentMessageDeliveryStatus = typing.Literal["active_turn", "mailbox"]
AgentMessageReceiptStatus = typing.Literal["accepted", "duplicate"]


class AgentIdentity(typing.Protocol):
    """描述消息投递所需的最小 Agent 身份。"""

    agent_id: str


class AgentMessageContext(typing.Protocol):
    """描述消息投递所需的轮次坐标和 Agent 身份。"""

    cid: str
    sid: str
    turn_id: str
    agent: AgentIdentity


@dataclass(frozen=True, slots=True)
class AgentMessageReceipt:
    """描述远程轮次对一项稳定消息标识的接收结果。"""

    status: AgentMessageReceiptStatus
    turn_id: str
    client_message_id: str

    def __post_init__(self) -> None:
        """校验回执中的远程关联标识。"""
        turn_id = str(self.turn_id or "").strip()
        client_message_id = str(self.client_message_id or "").strip()
        if self.status not in {"accepted", "duplicate"}:
            raise ValueError("agent message receipt status is invalid")
        if not turn_id or not client_message_id:
            raise ValueError("agent message receipt identifiers are required")
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "client_message_id", client_message_id)


class AgentMessageDeliveryPort(typing.Protocol):
    """定义向活动远程轮次投递输入所需能力。"""

    async def deliver(
        self,
        context: AgentMessageContext,
        turn_input: TurnInput,
    ) -> AgentMessageReceipt | None:
        """投递输入并返回匹配的远程接收回执。"""
        ...


__all__ = (
    "AgentMessageDeliveryPort",
    "AgentMessageContext",
    "AgentIdentity",
    "AgentMessageDeliveryStatus",
    "AgentMessageReceipt",
    "AgentMessageReceiptStatus",
)


if __name__ == '__main__':
    pass
