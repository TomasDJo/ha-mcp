"""Unit tests for calendar tools transport fallback."""

from unittest.mock import AsyncMock

import pytest

from ha_mcp.client.rest_client import HomeAssistantAPIError
from ha_mcp.tools.tools_calendar import _call_calendar_service


@pytest.mark.asyncio
async def test_call_calendar_service_uses_rest_on_success() -> None:
    client = AsyncMock()
    client.call_service.return_value = {"ok": True}

    result = await _call_calendar_service(
        client, "create_event", {"entity_id": "calendar.x"}
    )

    assert result == {"ok": True}
    client.call_service.assert_awaited_once_with(
        "calendar", "create_event", {"entity_id": "calendar.x"}
    )
    client.send_websocket_message.assert_not_called()


@pytest.mark.asyncio
async def test_call_calendar_service_falls_back_to_ws_on_400() -> None:
    client = AsyncMock()
    client.call_service.side_effect = HomeAssistantAPIError(
        "API error: 400 - Service call requires WebSocket", status_code=400
    )
    client.send_websocket_message.return_value = {"ok": True}

    service_data = {"entity_id": "calendar.x", "uid": "abc"}
    result = await _call_calendar_service(client, "delete_event", service_data)

    assert result == {"ok": True}
    client.send_websocket_message.assert_awaited_once_with(
        {
            "type": "call_service",
            "domain": "calendar",
            "service": "delete_event",
            "service_data": service_data,
        }
    )


@pytest.mark.asyncio
async def test_call_calendar_service_does_not_fall_back_on_non_400() -> None:
    client = AsyncMock()
    client.call_service.side_effect = HomeAssistantAPIError(
        "API error: 500", status_code=500
    )

    with pytest.raises(HomeAssistantAPIError):
        await _call_calendar_service(client, "update_event", {"entity_id": "calendar.x"})

    client.send_websocket_message.assert_not_called()
