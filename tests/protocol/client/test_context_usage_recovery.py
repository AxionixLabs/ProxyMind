import json
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import patch

import httpx
import pytest

from protocol.client import context_usage
from agent.adapters.protocol.context_usage import ProtocolContextUsageRecovery
from agent.ports.conversation import ContextUsageRecoveryError


@pytest.fixture
def usage_event(fixtures_root: Path):
    return json.loads((fixtures_root / 'protocol/context_usage.json').read_text(encoding='utf-8'))['event']


@pytest.mark.anyio
async def test_recovery_finishes_pages_without_treating_snapshot_as_cursor(usage_event):
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.path == '/mind-replay'
        assert request.url.params['vt'] == 'view-test'
        first = request.url.params['after_seq'] == '0'
        return httpx.Response(200, json={'ok': True, 'data': {
            'context_usage': usage_event, 'next_seq': 5 if first else 14,
            'has_more': first, 'gap': 'retained_prefix' if first else 'none',
        }})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with (
        patch.object(context_usage, 'open_report_session', AsyncMock(return_value={'vt': 'view-test'})),
        patch.object(context_usage.httpx, 'AsyncClient', return_value=client),
    ):
        event = await context_usage.recover_context_usage(usage_event['cid'], usage_event['sid'])
    assert event.event_seq == 12
    assert [request.url.params['after_seq'] for request in requests] == ['0', '5']
    assert client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize('changes', [
    {'context_usage': {}}, {'context_usage': 1}, {'has_more': 1},
    {'next_seq': True}, {'next_seq': 0, 'has_more': True},
    {'next_seq': 5}, {'gap': 'internal'},
])
async def test_recovery_rejects_invalid_or_incomplete_pages(usage_event, changes):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={'ok': True, 'data': {
            'context_usage': usage_event, 'next_seq': 14, 'has_more': False, 'gap': 'none', **changes,
        }},
    )))
    with (
        patch.object(context_usage, 'open_report_session', AsyncMock(return_value={'vt': 'view-test'})),
        patch.object(context_usage.httpx, 'AsyncClient', return_value=client),
        pytest.raises(ValueError),
    ):
        await context_usage.recover_context_usage(usage_event['cid'], usage_event['sid'])
    assert client.is_closed


@pytest.mark.anyio
async def test_recovery_rejects_cross_session_snapshot(usage_event):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={'ok': True, 'data': {
            'context_usage': usage_event, 'next_seq': 14, 'has_more': False, 'gap': 'none',
        }},
    )))
    with (
        patch.object(context_usage, 'open_report_session', AsyncMock(return_value={'vt': 'view-test'})),
        patch.object(context_usage.httpx, 'AsyncClient', return_value=client),
        pytest.raises(ValueError, match='identity'),
    ):
        await context_usage.recover_context_usage(usage_event['cid'], 'other')
    assert client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize('known', [False, True])
async def test_recovery_handles_empty_retained_history_and_unknown(usage_event, known):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={'ok': True, 'data': {
            'context_usage': usage_event if known else None, 'events': [],
            'next_seq': 20, 'cursor_floor': 20, 'has_more': False, 'gap': 'retained_prefix',
        }},
    )))
    with (
        patch.object(context_usage, 'open_report_session', AsyncMock(return_value={'vt': 'view-test'})),
        patch.object(context_usage.httpx, 'AsyncClient', return_value=client),
    ):
        event = await context_usage.recover_context_usage(usage_event['cid'], usage_event['sid'])
    assert (event is not None) == known
    assert client.is_closed


@pytest.mark.anyio
async def test_recovery_transport_error_does_not_expose_view_token():
    with (
        patch('agent.adapters.protocol.context_usage.recover_context_usage', AsyncMock(
            side_effect=httpx.ReadTimeout('private view URL'),
        )),
        pytest.raises(ContextUsageRecoveryError, match='^context usage recovery failed$') as failure,
    ):
        await ProtocolContextUsageRecovery().load('cid', 'sid')
    assert failure.value.__suppress_context__
