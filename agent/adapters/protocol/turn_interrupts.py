# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports import (
    ProtocolCommandClient,
    ProtocolCommandError,
)
from protocol.client.turn_control import TurnControlRequestError
from protocol.schema.identifiers import stable_request_id

__all__ = (
    "cancel_reconciliation_turn",
    "interrupt_approval_cancelled_turn",
)


async def cancel_reconciliation_turn(
    client: ProtocolCommandClient,
    *,
    cid: str,
    sid: str,
    turn_id: str,
    effect_id: str,
) -> bool:
    """使用稳定中断命令释放无法自动核对的持久轮次。"""
    return await _submit_interrupt(
        client,
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        purpose="reconciliation_cancel",
        discriminator=effect_id,
    )


async def interrupt_approval_cancelled_turn(
    client: ProtocolCommandClient,
    *,
    cid: str,
    sid: str,
    turn_id: str,
    call_id: str,
) -> bool:
    """中断本地审批取消对应的逻辑轮次。"""
    return await _submit_interrupt(
        client,
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        purpose="approval_cancel",
        discriminator=call_id,
    )


async def _submit_interrupt(
    client: ProtocolCommandClient,
    *,
    cid: str,
    sid: str,
    turn_id: str,
    purpose: str,
    discriminator: str,
) -> bool:
    """用稳定身份重试一次轮次中断并归一化释放结果。"""
    request_id = stable_request_id(
        purpose,
        cid,
        sid,
        turn_id,
        discriminator,
    )
    for attempt in range(2):
        try:
            response = await client.interrupt_turn(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                request_id=request_id,
            )
            return response.status in {"accepted", "turn_not_active"}
        except (TurnControlRequestError, ProtocolCommandError) as error:
            if error.code == "turn_not_active":
                return True
            if error.retryable and attempt == 0:
                continue
            return False
    return False


if __name__ == '__main__':
    pass
