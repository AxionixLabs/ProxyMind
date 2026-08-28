# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import AsyncMock

import pytest

from mind_app.runtime.turns.stream_effects import ToolResultDelivery
from mind_nova.requests.tools import ToolResultRequestError


def _delivery(
    *,
    post_result,
    get_status=None,
    reconcile_known_effect=None,
    post_reconciliation=None,
    sleep=None,
) -> ToolResultDelivery:
    """构造只暴露本次断言所需端口的交付对象。"""
    kwargs = {
        "reconcile_known_effect": (
            reconcile_known_effect or AsyncMock(return_value=False)
        ),
        "post_result": post_result,
        "get_status": get_status or AsyncMock(),
        "post_reconciliation": post_reconciliation or AsyncMock(),
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    return ToolResultDelivery(**kwargs)


async def _deliver(
    delivery: ToolResultDelivery,
    *,
    result=None,
) -> None:
    """使用稳定工具调用身份投递一份测试结果。"""
    await delivery.deliver(
        "cid-test",
        "sid-test",
        "call-test",
        "test_tool",
        True,
        {"value": 1} if result is None else result,
        additional_context=("context",),
        tool_arguments={"input": 2},
    )


@pytest.mark.anyio
async def test_delivery_deduplicates_concurrent_identical_results() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    posts = []

    async def post_result(*args, **kwargs) -> None:
        posts.append((args, kwargs))
        started.set()
        await release.wait()

    delivery = _delivery(post_result=post_result)
    first = asyncio.create_task(_deliver(delivery))
    await started.wait()
    second = asyncio.create_task(_deliver(delivery))
    await asyncio.sleep(0)

    release.set()
    await asyncio.gather(first, second)

    assert len(posts) == 1
    assert posts[0][1]["request_id"].startswith("tool_result_")


@pytest.mark.anyio
async def test_delivery_rejects_different_result_for_same_call() -> None:
    post_result = AsyncMock(return_value=None)
    delivery = _delivery(post_result=post_result)
    await _deliver(delivery)

    with pytest.raises(ToolResultRequestError) as caught:
        await _deliver(delivery, result={"value": 2})

    assert caught.value.code == "tool_result_local_conflict"
    assert caught.value.details == {"call_id": "call-test"}
    post_result.assert_awaited_once()


@pytest.mark.anyio
async def test_delivery_retries_unknown_ack_with_same_request_id() -> None:
    request_ids = []
    sleeps = []

    async def post_result(*_args, **kwargs) -> None:
        request_ids.append(kwargs["request_id"])
        if len(request_ids) == 1:
            raise ToolResultRequestError(
                "tool_result_ack_invalid",
                "tool result acknowledgement is invalid",
            )

    get_status = AsyncMock(return_value={
        "tool_status": "waiting_result",
        "result_received": False,
        "reconciliation_required": False,
    })

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    delivery = _delivery(
        post_result=post_result,
        get_status=get_status,
        sleep=sleep,
    )
    await _deliver(delivery)

    assert len(request_ids) == 2
    assert request_ids[0] == request_ids[1]
    assert sleeps == [0.1]
    get_status.assert_awaited_once_with(
        cid="cid-test",
        sid="sid-test",
        call_id="call-test",
    )


@pytest.mark.anyio
async def test_delivery_reconciles_failed_result_with_frozen_payload() -> None:
    async def post_result(*_args, **_kwargs) -> None:
        raise ToolResultRequestError(
            "tool_result_reconciliation_required",
            "tool result requires effect reconciliation",
            retryable=True,
        )

    get_status = AsyncMock(return_value={
        "tool_status": "waiting_result",
        "result_received": False,
        "reconciliation_required": True,
        "effect_id": "effect-test",
    })
    reconcile_known_effect = AsyncMock(return_value=False)
    post_reconciliation = AsyncMock(return_value={})

    async def sleep(_delay: float) -> None:
        return None

    delivery = _delivery(
        post_result=post_result,
        get_status=get_status,
        reconcile_known_effect=reconcile_known_effect,
        post_reconciliation=post_reconciliation,
        sleep=sleep,
    )
    await _deliver(
        delivery,
        result={"executed": False, "error": "execution denied"},
    )

    reconcile_known_effect.assert_awaited_once_with("effect-test")
    post_reconciliation.assert_awaited_once()
    reconciliation = post_reconciliation.await_args.kwargs
    assert reconciliation["effect_id"] == "effect-test"
    assert reconciliation["resolution"] == "failed"
    assert reconciliation["error"] == "execution denied"
    assert reconciliation["result_payload"]["request_id"].startswith(
        "tool_result_"
    )
    assert reconciliation["result_payload"]["result"]["data"] == {
        "executed": False,
        "error": "execution denied",
    }
