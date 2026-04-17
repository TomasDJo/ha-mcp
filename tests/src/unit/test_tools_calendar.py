"""Unit tests for calendar tools transport fallback, RRULE builder, and recurrence support."""

from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from ha_mcp.tools.tools_calendar import CalendarTools, _build_rrule, _call_calendar_service


# --- _call_calendar_service tests ---


@pytest.mark.asyncio
async def test_call_calendar_service_uses_websocket() -> None:
    client = AsyncMock()
    client.send_websocket_message.return_value = {"ok": True}

    result = await _call_calendar_service(
        client, "create_event", {"entity_id": "calendar.x"}
    )

    assert result == {"ok": True}
    client.send_websocket_message.assert_awaited_once_with(
        {
            "type": "call_service",
            "domain": "calendar",
            "service": "create_event",
            "service_data": {"entity_id": "calendar.x"},
        }
    )


@pytest.mark.asyncio
async def test_call_calendar_service_passes_rrule_via_websocket() -> None:
    client = AsyncMock()
    client.send_websocket_message.return_value = {"ok": True}

    service_data = {"entity_id": "calendar.x", "rrule": "FREQ=WEEKLY"}
    result = await _call_calendar_service(client, "create_event", service_data)

    assert result == {"ok": True}
    sent = client.send_websocket_message.call_args[0][0]
    assert sent["service_data"]["rrule"] == "FREQ=WEEKLY"


@pytest.mark.asyncio
async def test_call_calendar_service_delete() -> None:
    client = AsyncMock()
    client.send_websocket_message.return_value = {"ok": True}

    service_data = {"entity_id": "calendar.x", "uid": "abc"}
    result = await _call_calendar_service(client, "delete_event", service_data)

    assert result == {"ok": True}
    sent = client.send_websocket_message.call_args[0][0]
    assert sent["service"] == "delete_event"
    assert sent["service_data"]["uid"] == "abc"


# --- _build_rrule tests ---


def test_build_rrule_simple_daily() -> None:
    assert _build_rrule("DAILY") == "FREQ=DAILY"


def test_build_rrule_weekly_with_interval() -> None:
    assert _build_rrule("WEEKLY", interval=2) == "FREQ=WEEKLY;INTERVAL=2"


def test_build_rrule_weekly_with_days_and_count() -> None:
    result = _build_rrule("WEEKLY", by_day="MO,WE,FR", count=10)
    assert result == "FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=10"


def test_build_rrule_monthly_with_until() -> None:
    result = _build_rrule("MONTHLY", until="2024-12-31")
    assert result == "FREQ=MONTHLY;UNTIL=20241231T235959Z"


def test_build_rrule_yearly_full() -> None:
    result = _build_rrule("YEARLY", interval=1, count=5)
    assert result == "FREQ=YEARLY;COUNT=5"


def test_build_rrule_until_with_datetime() -> None:
    result = _build_rrule("DAILY", until="2024-06-15T18:00:00")
    assert result == "FREQ=DAILY;UNTIL=20240615T180000"


def test_build_rrule_interval_1_omitted() -> None:
    result = _build_rrule("DAILY", interval=1)
    assert result == "FREQ=DAILY"


def test_build_rrule_rejects_count_and_until() -> None:
    with pytest.raises(ValueError, match="Cannot specify both"):
        _build_rrule("WEEKLY", count=5, until="2024-12-31")


def test_build_rrule_rejects_invalid_day() -> None:
    with pytest.raises(ValueError, match="Invalid day"):
        _build_rrule("WEEKLY", by_day="MO,XX")


def test_build_rrule_normalizes_day_case() -> None:
    result = _build_rrule("WEEKLY", by_day="mo, we, fr")
    assert result == "FREQ=WEEKLY;BYDAY=MO,WE,FR"


# --- update_calendar_event recurrence tests ---


def _make_tools() -> tuple[CalendarTools, AsyncMock]:
    client = AsyncMock()
    client.call_service.return_value = {"ok": True}
    client.send_websocket_message.return_value = {"ok": True}
    return CalendarTools(client), client


@pytest.mark.asyncio
async def test_update_event_sets_rrule_from_frequency() -> None:
    tools, client = _make_tools()

    result = await tools.ha_config_update_calendar_event(
        entity_id="calendar.family",
        uid="event-123",
        recurrence_frequency="WEEKLY",
        recurrence_by_day="MO,WE",
        recurrence_count=10,
    )

    assert result["success"] is True
    assert result["updated_fields"]["rrule"] == "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10"


@pytest.mark.asyncio
async def test_update_event_removes_recurrence() -> None:
    tools, client = _make_tools()

    result = await tools.ha_config_update_calendar_event(
        entity_id="calendar.family",
        uid="event-123",
        recurrence_remove=True,
    )

    assert result["success"] is True
    assert result["updated_fields"]["rrule"] == ""


@pytest.mark.asyncio
async def test_update_event_rejects_remove_and_frequency() -> None:
    tools, _ = _make_tools()

    with pytest.raises(ToolError, match="Cannot set both"):
        await tools.ha_config_update_calendar_event(
            entity_id="calendar.family",
            uid="event-123",
            recurrence_remove=True,
            recurrence_frequency="DAILY",
        )


@pytest.mark.asyncio
async def test_update_event_rejects_sub_params_without_frequency() -> None:
    tools, _ = _make_tools()

    with pytest.raises(ToolError, match="recurrence_frequency is required"):
        await tools.ha_config_update_calendar_event(
            entity_id="calendar.family",
            uid="event-123",
            recurrence_by_day="MO,WE",
        )


@pytest.mark.asyncio
async def test_update_event_rejects_no_fields() -> None:
    tools, _ = _make_tools()

    with pytest.raises(ToolError, match="No fields to update"):
        await tools.ha_config_update_calendar_event(
            entity_id="calendar.family",
            uid="event-123",
        )


# --- create_calendar_event recurrence tests ---


@pytest.mark.asyncio
async def test_create_event_includes_rrule() -> None:
    tools, client = _make_tools()

    result = await tools.ha_config_set_calendar_event(
        entity_id="calendar.work",
        summary="Standup",
        start="2024-01-02T09:00:00",
        end="2024-01-02T09:15:00",
        recurrence_frequency="DAILY",
        recurrence_by_day="MO,TU,WE,TH,FR",
        recurrence_until="2024-03-31",
    )

    assert result["success"] is True
    assert result["event"]["rrule"] == "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;UNTIL=20240331T235959Z"


@pytest.mark.asyncio
async def test_create_event_no_rrule_when_no_recurrence() -> None:
    tools, client = _make_tools()

    result = await tools.ha_config_set_calendar_event(
        entity_id="calendar.family",
        summary="Dentist",
        start="2024-01-15T10:00:00",
        end="2024-01-15T11:00:00",
    )

    assert result["success"] is True
    assert "rrule" not in result["event"]


@pytest.mark.asyncio
async def test_create_event_rejects_sub_params_without_frequency() -> None:
    tools, _ = _make_tools()

    with pytest.raises(ToolError, match="recurrence_frequency is required"):
        await tools.ha_config_set_calendar_event(
            entity_id="calendar.family",
            summary="Test",
            start="2024-01-15T10:00:00",
            end="2024-01-15T11:00:00",
            recurrence_count=5,
        )
