"""
Calendar event management tools for Home Assistant MCP server.

This module provides tools for managing calendar events in Home Assistant,
including retrieving events, creating events, and deleting events.

Use ha_search_entities(query='calendar', domain_filter='calendar') to find calendar entities.
"""

import logging
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from pydantic import Field

from ..client.rest_client import HomeAssistantAPIError
from ..errors import ErrorCode, create_error_response
from .helpers import (
    exception_to_structured_error,
    log_tool_usage,
    raise_tool_error,
    register_tool_methods,
)

logger = logging.getLogger(__name__)


async def _call_calendar_service(
    client: Any, service: str, service_data: dict[str, Any]
) -> Any:
    """Call a calendar.* service, falling back to WebSocket on HTTP 400.

    HA's ``calendar.delete_event`` and ``calendar.update_event`` services are
    WebSocket-only and return 400 via REST. ``calendar.create_event`` works
    over REST. Try REST first; on 400, retry via WS so all three paths work
    through a single helper.
    """
    try:
        return await client.call_service("calendar", service, service_data)
    except HomeAssistantAPIError as err:
        if err.status_code != 400:
            raise
        logger.debug(
            f"calendar.{service} returned 400 over REST; retrying via WebSocket"
        )
        return await client.send_websocket_message(
            {
                "type": "call_service",
                "domain": "calendar",
                "service": service,
                "service_data": service_data,
            }
        )


_VALID_DAYS = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}


def _build_rrule(
    frequency: str,
    interval: int | None = None,
    count: int | None = None,
    until: str | None = None,
    by_day: str | None = None,
) -> str:
    """Build an RFC 5545 RRULE string from user-friendly parameters."""
    parts = [f"FREQ={frequency}"]

    if interval is not None and interval > 1:
        parts.append(f"INTERVAL={interval}")

    if by_day:
        days = [d.strip().upper() for d in by_day.split(",")]
        invalid = [d for d in days if d not in _VALID_DAYS]
        if invalid:
            raise ValueError(
                f"Invalid day(s): {', '.join(invalid)}. "
                f"Valid values: {', '.join(sorted(_VALID_DAYS))}"
            )
        parts.append(f"BYDAY={','.join(days)}")

    if count is not None and until is not None:
        raise ValueError("Cannot specify both 'recurrence_count' and 'recurrence_until'")
    if count is not None:
        parts.append(f"COUNT={count}")
    elif until is not None:
        dt = until.replace("-", "").replace(":", "").replace("T", "T")
        if "T" not in dt:
            dt += "T235959Z"
        parts.append(f"UNTIL={dt}")

    return ";".join(parts)


class CalendarTools:
    """Calendar event management tools for Home Assistant."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @tool(
        name="ha_config_get_calendar_events",
        tags={"Calendar"},
        annotations={"idempotentHint": True, "readOnlyHint": True, "title": "Get Calendar Events"},
    )
    @log_tool_usage
    async def ha_config_get_calendar_events(
        self,
        entity_id: Annotated[
            str, Field(description="Calendar entity ID (e.g., 'calendar.family')")
        ],
        start: Annotated[
            str | None,
            Field(
                description="Start datetime in ISO format (default: now)", default=None
            ),
        ] = None,
        end: Annotated[
            str | None,
            Field(
                description="End datetime in ISO format (default: 7 days from start)",
                default=None,
            ),
        ] = None,
        max_results: Annotated[
            int,
            Field(description="Maximum number of events to return", default=20),
        ] = 20,
    ) -> dict[str, Any]:
        """
        Retrieve calendar events from a calendar entity.

        Retrieves calendar events within a specified time range.

        **Parameters:**
        - entity_id: Calendar entity ID (e.g., 'calendar.family')
        - start: Start datetime in ISO format (default: now)
        - end: End datetime in ISO format (default: 7 days from start)
        - max_results: Maximum number of events to return (default: 20)

        **Example Usage:**
        ```python
        # Get events for the next week
        events = ha_config_get_calendar_events("calendar.family")

        # Get events for a specific date range
        events = ha_config_get_calendar_events(
            "calendar.work",
            start="2024-01-01T00:00:00",
            end="2024-01-31T23:59:59"
        )
        ```

        **Note:** To find calendar entities, use ha_search_entities(query='calendar', domain_filter='calendar')

        **Returns:**
        - List of calendar events with summary, start, end, description, location
        """
        try:
            # Validate entity_id
            if not entity_id.startswith("calendar."):
                raise_tool_error(create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    f"Invalid calendar entity ID: {entity_id}. Must start with 'calendar.'",
                    context={"entity_id": entity_id},
                    suggestions=[
                        "Use ha_search_entities(query='calendar', domain_filter='calendar') to find calendar entities",
                        "Calendar entity IDs start with 'calendar.' prefix",
                    ],
                ))

            # Set default time range if not provided
            now = datetime.now()
            if start is None:
                start = now.isoformat()
            if end is None:
                end_date = now + timedelta(days=7)
                end = end_date.isoformat()

            # Build the API endpoint for calendar events
            # Home Assistant uses: GET /api/calendars/{entity_id}?start=...&end=...
            params = {"start": start, "end": end}

            # Use the REST client to fetch calendar events
            # The endpoint is /calendars/{entity_id} (note: without /api prefix as client adds it)
            response = await self._client._request(
                "GET", f"/calendars/{entity_id}", params=params
            )

            # Response is a list of events
            events = response if isinstance(response, list) else []

            # Limit results
            limited_events = events[:max_results]

            return {
                "success": True,
                "entity_id": entity_id,
                "events": limited_events,
                "count": len(limited_events),
                "total_available": len(events),
                "time_range": {
                    "start": start,
                    "end": end,
                },
                "message": f"Retrieved {len(limited_events)} event(s) from {entity_id}",
            }

        except ToolError:
            raise
        except Exception as error:
            logger.error(f"Failed to get calendar events for {entity_id}: {error}")

            # Provide helpful error messages
            suggestions = [
                f"Verify calendar entity '{entity_id}' exists using ha_search_entities(query='calendar', domain_filter='calendar')",
                "Check start/end datetime format (ISO 8601)",
                "Ensure calendar integration supports event retrieval",
            ]

            error_str = str(error)
            if "404" in error_str or "not found" in error_str.lower():
                suggestions.insert(0, f"Calendar entity '{entity_id}' not found")

            exception_to_structured_error(error, context={"entity_id": entity_id}, suggestions=suggestions)

    @tool(
        name="ha_config_set_calendar_event",
        tags={"Calendar"},
        annotations={"destructiveHint": True, "title": "Create or Update Calendar Event"},
    )
    @log_tool_usage
    async def ha_config_set_calendar_event(
        self,
        entity_id: Annotated[
            str, Field(description="Calendar entity ID (e.g., 'calendar.family')")
        ],
        summary: Annotated[str, Field(description="Event title/summary")],
        start: Annotated[
            str, Field(description="Event start datetime in ISO format")
        ],
        end: Annotated[str, Field(description="Event end datetime in ISO format")],
        description: Annotated[
            str | None,
            Field(description="Optional event description", default=None),
        ] = None,
        location: Annotated[
            str | None, Field(description="Optional event location", default=None)
        ] = None,
        recurrence_frequency: Annotated[
            Literal["DAILY", "WEEKLY", "MONTHLY", "YEARLY"] | None,
            Field(
                description="Recurrence frequency. Set this to make the event recurring.",
                default=None,
            ),
        ] = None,
        recurrence_interval: Annotated[
            int | None,
            Field(
                description="Repeat every N periods (e.g., 2 = every other week). Defaults to 1.",
                default=None,
                ge=1,
            ),
        ] = None,
        recurrence_count: Annotated[
            int | None,
            Field(
                description="Total number of occurrences. Mutually exclusive with recurrence_until.",
                default=None,
                ge=1,
            ),
        ] = None,
        recurrence_until: Annotated[
            str | None,
            Field(
                description="Recurrence end date in ISO format (e.g., '2024-12-31'). Mutually exclusive with recurrence_count.",
                default=None,
            ),
        ] = None,
        recurrence_by_day: Annotated[
            str | None,
            Field(
                description="Comma-separated days for weekly recurrence (e.g., 'MO,WE,FR'). Valid: MO,TU,WE,TH,FR,SA,SU.",
                default=None,
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Create a new event in a calendar, optionally recurring.

        Creates a calendar event using the calendar.create_event service.

        **Parameters:**
        - entity_id: Calendar entity ID (e.g., 'calendar.family')
        - summary: Event title/summary
        - start: Event start datetime in ISO format
        - end: Event end datetime in ISO format
        - description: Optional event description
        - location: Optional event location
        - recurrence_frequency: DAILY, WEEKLY, MONTHLY, or YEARLY (makes the event recurring)
        - recurrence_interval: Every N periods (default 1, e.g., 2 = every other week)
        - recurrence_count: Total occurrences (mutually exclusive with recurrence_until)
        - recurrence_until: End date for recurrence (mutually exclusive with recurrence_count)
        - recurrence_by_day: Days for weekly recurrence (e.g., 'MO,WE,FR')

        **Example Usage:**
        ```python
        # Create a simple event
        result = ha_config_set_calendar_event(
            "calendar.family",
            summary="Doctor appointment",
            start="2024-01-15T14:00:00",
            end="2024-01-15T15:00:00"
        )

        # Create a weekly recurring event every Monday and Wednesday, 10 times
        result = ha_config_set_calendar_event(
            "calendar.work",
            summary="Team meeting",
            start="2024-01-16T10:00:00",
            end="2024-01-16T11:00:00",
            description="Weekly sync meeting",
            location="Conference Room A",
            recurrence_frequency="WEEKLY",
            recurrence_by_day="MO,WE",
            recurrence_count=10,
        )

        # Create a daily standup until end of quarter
        result = ha_config_set_calendar_event(
            "calendar.work",
            summary="Daily standup",
            start="2024-01-02T09:00:00",
            end="2024-01-02T09:15:00",
            recurrence_frequency="DAILY",
            recurrence_by_day="MO,TU,WE,TH,FR",
            recurrence_until="2024-03-31",
        )
        ```

        **Returns:**
        - Success status and event details
        """
        try:
            # Validate entity_id
            if not entity_id.startswith("calendar."):
                raise_tool_error(create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    f"Invalid calendar entity ID: {entity_id}. Must start with 'calendar.'",
                    context={"entity_id": entity_id},
                    suggestions=[
                        "Use ha_search_entities(query='calendar', domain_filter='calendar') to find calendar entities",
                        "Calendar entity IDs start with 'calendar.' prefix",
                    ],
                ))

            # Build service data
            service_data: dict[str, Any] = {
                "entity_id": entity_id,
                "summary": summary,
                "start_date_time": start,
                "end_date_time": end,
            }

            if description:
                service_data["description"] = description
            if location:
                service_data["location"] = location

            rrule: str | None = None
            if recurrence_frequency:
                try:
                    rrule = _build_rrule(
                        frequency=recurrence_frequency,
                        interval=recurrence_interval,
                        count=recurrence_count,
                        until=recurrence_until,
                        by_day=recurrence_by_day,
                    )
                except ValueError as ve:
                    raise_tool_error(create_error_response(
                        ErrorCode.VALIDATION_INVALID_PARAMETER,
                        str(ve),
                        context={"recurrence_frequency": recurrence_frequency},
                    ))
                service_data["rrule"] = rrule
            elif any(p is not None for p in (recurrence_interval, recurrence_count, recurrence_until, recurrence_by_day)):
                raise_tool_error(create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    "recurrence_frequency is required when using other recurrence parameters",
                    suggestions=["Set recurrence_frequency to DAILY, WEEKLY, MONTHLY, or YEARLY"],
                ))

            # Call the calendar.create_event service (REST, with WS fallback)
            result = await _call_calendar_service(self._client, "create_event", service_data)

            event_info: dict[str, Any] = {
                "summary": summary,
                "start": start,
                "end": end,
                "description": description,
                "location": location,
            }
            if rrule:
                event_info["rrule"] = rrule

            return {
                "success": True,
                "entity_id": entity_id,
                "event": event_info,
                "result": result,
                "message": f"Successfully created event '{summary}' in {entity_id}",
            }

        except ToolError:
            raise
        except Exception as error:
            logger.error(f"Failed to create calendar event in {entity_id}: {error}")

            suggestions = [
                f"Verify calendar entity '{entity_id}' exists and supports event creation",
                "Check datetime format (ISO 8601)",
                "Ensure end time is after start time",
                "Some calendar integrations may be read-only",
            ]

            error_str = str(error)
            if "404" in error_str or "not found" in error_str.lower():
                suggestions.insert(0, f"Calendar entity '{entity_id}' not found")
            if "not supported" in error_str.lower():
                suggestions.insert(0, "This calendar does not support event creation")

            exception_to_structured_error(error, context={"entity_id": entity_id}, suggestions=suggestions)

    @tool(
        name="ha_config_remove_calendar_event",
        tags={"Calendar"},
        annotations={"destructiveHint": True, "idempotentHint": True, "title": "Remove Calendar Event"},
    )
    @log_tool_usage
    async def ha_config_remove_calendar_event(
        self,
        entity_id: Annotated[
            str, Field(description="Calendar entity ID (e.g., 'calendar.family')")
        ],
        uid: Annotated[str, Field(description="Unique identifier of the event to delete")],
        recurrence_id: Annotated[
            str | None,
            Field(description="Optional recurrence ID for recurring events", default=None),
        ] = None,
        recurrence_range: Annotated[
            str | None,
            Field(
                description="Optional recurrence range ('THIS_AND_FUTURE' to delete this and future occurrences)",
                default=None,
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Delete an event from a calendar.

        Deletes a calendar event using the calendar.delete_event service.

        **Parameters:**
        - entity_id: Calendar entity ID (e.g., 'calendar.family')
        - uid: Unique identifier of the event to delete
        - recurrence_id: Optional recurrence ID for recurring events
        - recurrence_range: Optional recurrence range ('THIS_AND_FUTURE' to delete this and future occurrences)

        **Example Usage:**
        ```python
        # Delete a single event
        result = ha_config_remove_calendar_event(
            "calendar.family",
            uid="event-12345"
        )

        # Delete a recurring event instance and future occurrences
        result = ha_config_remove_calendar_event(
            "calendar.work",
            uid="recurring-event-67890",
            recurrence_id="20240115T100000",
            recurrence_range="THIS_AND_FUTURE"
        )
        ```

        **Note:**
        To get the event UID, first use ha_config_get_calendar_events() to list events.
        The UID is returned in each event's data.

        **Returns:**
        - Success status and deletion confirmation
        """
        try:
            # Validate entity_id
            if not entity_id.startswith("calendar."):
                raise_tool_error(create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    f"Invalid calendar entity ID: {entity_id}. Must start with 'calendar.'",
                    context={"entity_id": entity_id},
                    suggestions=[
                        "Use ha_search_entities(query='calendar', domain_filter='calendar') to find calendar entities",
                        "Calendar entity IDs start with 'calendar.' prefix",
                    ],
                ))

            # Build service data
            service_data: dict[str, Any] = {
                "entity_id": entity_id,
                "uid": uid,
            }

            if recurrence_id:
                service_data["recurrence_id"] = recurrence_id
            if recurrence_range:
                service_data["recurrence_range"] = recurrence_range

            # calendar.delete_event is WebSocket-only; helper falls back to WS on 400
            result = await _call_calendar_service(self._client, "delete_event", service_data)

            return {
                "success": True,
                "entity_id": entity_id,
                "uid": uid,
                "recurrence_id": recurrence_id,
                "recurrence_range": recurrence_range,
                "result": result,
                "message": f"Successfully deleted event '{uid}' from {entity_id}",
            }

        except ToolError:
            raise
        except Exception as error:
            logger.error(f"Failed to delete calendar event from {entity_id}: {error}")

            suggestions = [
                f"Verify calendar entity '{entity_id}' exists",
                f"Verify event with UID '{uid}' exists in the calendar",
                "Use ha_config_get_calendar_events() to find the correct event UID",
                "Some calendar integrations may not support event deletion",
            ]

            error_str = str(error)
            if "404" in error_str or "not found" in error_str.lower():
                suggestions.insert(
                    0, f"Calendar entity '{entity_id}' or event '{uid}' not found"
                )
            if "not supported" in error_str.lower():
                suggestions.insert(0, "This calendar does not support event deletion")

            exception_to_structured_error(error, context={"entity_id": entity_id, "uid": uid}, suggestions=suggestions)


    @tool(
        name="ha_config_update_calendar_event",
        tags={"Calendar"},
        annotations={"destructiveHint": True, "idempotentHint": True, "title": "Update Calendar Event"},
    )
    @log_tool_usage
    async def ha_config_update_calendar_event(
        self,
        entity_id: Annotated[
            str, Field(description="Calendar entity ID (e.g., 'calendar.family')")
        ],
        uid: Annotated[str, Field(description="Unique identifier of the event to update")],
        summary: Annotated[
            str | None, Field(description="New event title/summary", default=None)
        ] = None,
        description: Annotated[
            str | None, Field(description="New event description", default=None)
        ] = None,
        location: Annotated[
            str | None, Field(description="New event location", default=None)
        ] = None,
        start: Annotated[
            str | None,
            Field(description="New event start datetime in ISO format", default=None),
        ] = None,
        end: Annotated[
            str | None,
            Field(description="New event end datetime in ISO format", default=None),
        ] = None,
        recurrence_id: Annotated[
            str | None,
            Field(description="Optional recurrence ID for recurring events", default=None),
        ] = None,
        recurrence_range: Annotated[
            str | None,
            Field(
                description="Optional recurrence range ('THIS_AND_FUTURE' to apply to this and future occurrences)",
                default=None,
            ),
        ] = None,
        recurrence_frequency: Annotated[
            Literal["DAILY", "WEEKLY", "MONTHLY", "YEARLY"] | None,
            Field(
                description="Set or change recurrence frequency. Mutually exclusive with recurrence_remove.",
                default=None,
            ),
        ] = None,
        recurrence_interval: Annotated[
            int | None,
            Field(
                description="Repeat every N periods (e.g., 2 = every other week). Defaults to 1.",
                default=None,
                ge=1,
            ),
        ] = None,
        recurrence_count: Annotated[
            int | None,
            Field(
                description="Total number of occurrences. Mutually exclusive with recurrence_until.",
                default=None,
                ge=1,
            ),
        ] = None,
        recurrence_until: Annotated[
            str | None,
            Field(
                description="Recurrence end date in ISO format. Mutually exclusive with recurrence_count.",
                default=None,
            ),
        ] = None,
        recurrence_by_day: Annotated[
            str | None,
            Field(
                description="Comma-separated days for weekly recurrence (e.g., 'MO,WE,FR').",
                default=None,
            ),
        ] = None,
        recurrence_remove: Annotated[
            bool | None,
            Field(
                description="Set to true to remove recurrence and make the event a single occurrence. Mutually exclusive with recurrence_frequency.",
                default=None,
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Update an existing calendar event.

        Updates a calendar event using the calendar.update_event service.
        This service is WebSocket-only in Home Assistant; the tool automatically
        uses the WebSocket transport.

        **Parameters:**
        - entity_id: Calendar entity ID (e.g., 'calendar.family')
        - uid: Unique identifier of the event to update
        - summary, description, location: New field values (optional)
        - start, end: New start/end datetime in ISO format (optional)
        - recurrence_id, recurrence_range: For recurring event instances (optional)
        - recurrence_frequency, recurrence_interval, recurrence_count, recurrence_until, recurrence_by_day: Set or change recurrence (optional)
        - recurrence_remove: Set to true to remove recurrence entirely (optional)

        **Returns:**
        - Success status and the fields that were updated
        """
        try:
            if not entity_id.startswith("calendar."):
                raise_tool_error(create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    f"Invalid calendar entity ID: {entity_id}. Must start with 'calendar.'",
                    context={"entity_id": entity_id},
                    suggestions=[
                        "Use ha_search_entities(query='calendar', domain_filter='calendar') to find calendar entities",
                        "Calendar entity IDs start with 'calendar.' prefix",
                    ],
                ))

            service_data: dict[str, Any] = {"entity_id": entity_id, "uid": uid}
            if summary is not None:
                service_data["summary"] = summary
            if description is not None:
                service_data["description"] = description
            if location is not None:
                service_data["location"] = location
            if start is not None:
                service_data["start_date_time"] = start
            if end is not None:
                service_data["end_date_time"] = end
            if recurrence_id:
                service_data["recurrence_id"] = recurrence_id
            if recurrence_range:
                service_data["recurrence_range"] = recurrence_range

            if recurrence_remove and recurrence_frequency:
                raise_tool_error(create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    "Cannot set both recurrence_remove and recurrence_frequency",
                    suggestions=["Use recurrence_remove=true to remove recurrence, or recurrence_frequency to set/change it"],
                ))

            if recurrence_remove:
                service_data["rrule"] = ""
            elif recurrence_frequency:
                try:
                    service_data["rrule"] = _build_rrule(
                        frequency=recurrence_frequency,
                        interval=recurrence_interval,
                        count=recurrence_count,
                        until=recurrence_until,
                        by_day=recurrence_by_day,
                    )
                except ValueError as ve:
                    raise_tool_error(create_error_response(
                        ErrorCode.VALIDATION_INVALID_PARAMETER,
                        str(ve),
                        context={"recurrence_frequency": recurrence_frequency},
                    ))
            elif any(p is not None for p in (recurrence_interval, recurrence_count, recurrence_until, recurrence_by_day)):
                raise_tool_error(create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    "recurrence_frequency is required when using other recurrence parameters",
                    suggestions=["Set recurrence_frequency to DAILY, WEEKLY, MONTHLY, or YEARLY"],
                ))

            if len(service_data) <= 2:
                raise_tool_error(create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    "No fields to update. Provide at least one of: summary, description, location, start, end, recurrence_frequency, or recurrence_remove.",
                    context={"entity_id": entity_id, "uid": uid},
                ))

            result = await _call_calendar_service(self._client, "update_event", service_data)

            return {
                "success": True,
                "entity_id": entity_id,
                "uid": uid,
                "updated_fields": {
                    k: v for k, v in service_data.items() if k not in ("entity_id", "uid")
                },
                "result": result,
                "message": f"Successfully updated event '{uid}' in {entity_id}",
            }

        except ToolError:
            raise
        except Exception as error:
            logger.error(f"Failed to update calendar event in {entity_id}: {error}")

            suggestions = [
                f"Verify calendar entity '{entity_id}' exists",
                f"Verify event with UID '{uid}' exists in the calendar",
                "Use ha_config_get_calendar_events() to find the correct event UID",
                "Some calendar integrations may not support event updates",
            ]

            error_str = str(error)
            if "404" in error_str or "not found" in error_str.lower():
                suggestions.insert(0, f"Calendar entity '{entity_id}' or event '{uid}' not found")
            if "not supported" in error_str.lower():
                suggestions.insert(0, "This calendar does not support event updates")

            exception_to_structured_error(
                error, context={"entity_id": entity_id, "uid": uid}, suggestions=suggestions
            )


def register_calendar_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register calendar management tools with the MCP server."""
    register_tool_methods(mcp, CalendarTools(client))
