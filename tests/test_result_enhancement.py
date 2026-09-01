# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from infrastructure.services.tool_result_enhancement import enhance_tool_result


@pytest.mark.anyio
async def test_nexus_result_bypasses_enhancement_and_hidden_recording() -> None:
    fields = {
        "ok": True,
        "tool": "nexus_http_request",
        "data": {"status_code": 200},
    }
    reporter = SimpleNamespace(
        record=AsyncMock(),
        display=AsyncMock(),
        begin_status=AsyncMock(),
        end_status=AsyncMock(),
    )

    result = await enhance_tool_result(
        pref_config={},
        name="nexus_http_request",
        result_fields=fields,
        ok=True,
        reporter=reporter,
    )

    assert result is fields
    reporter.record.assert_not_awaited()
    reporter.display.assert_not_awaited()
    reporter.begin_status.assert_not_awaited()
    reporter.end_status.assert_not_awaited()
