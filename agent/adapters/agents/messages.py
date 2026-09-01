# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from observability import observe_exception
from agent.ports.agent_messages import (
    AgentMessageContext,
    AgentMessageReceipt,
    AgentMessageReceiptStatus,
)
from protocol.client.turn_control import (
    TurnControlRequestError,
    TurnControlStatus,
    steer_turn,
)
from protocol.schema.identifiers import new_request_id
from protocol.schema.turn_inputs import TurnInput

__all__ = ("SteeringMessageDelivery",)

STEERING_ATTEMPTS = 2


class SteeringMessageDelivery:
    """通过 Protocol Client 的 steer 操作投递活动轮次输入。"""

    async def deliver(
        self,
        context: AgentMessageContext,
        turn_input: TurnInput,
    ) -> AgentMessageReceipt | None:
        """尝试投递输入，不可用时返回空回执。"""
        response = None
        request_id = new_request_id("steer")

        for attempt in range(STEERING_ATTEMPTS):
            try:
                response = await steer_turn(
                    cid=context.cid,
                    sid=context.sid,
                    turn_id=context.turn_id,
                    turn_input=turn_input,
                    request_id=request_id,
                )
                break
            except TurnControlRequestError as error:
                if attempt + 1 < STEERING_ATTEMPTS:
                    continue
                observe_exception(
                    "subagent.message.steer_failed",
                    error,
                    level="WARNING",
                    agent_id=context.agent.agent_id,
                    turn_id=context.turn_id,
                )

        if response is None:
            return None
        receipt_status = _receipt_status(response.status)
        if receipt_status is None:
            return None
        return AgentMessageReceipt(
            status=receipt_status,
            turn_id=response.turn_id,
            client_message_id=response.client_message_id,
        )


def _receipt_status(value: TurnControlStatus) -> AgentMessageReceiptStatus | None:
    """把远端控制状态收窄为可确认的消息回执状态。"""
    if value == "accepted":
        return "accepted"
    if value == "duplicate":
        return "duplicate"
    return None


if __name__ == '__main__':
    pass
