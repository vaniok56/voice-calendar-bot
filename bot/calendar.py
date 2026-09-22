import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
MAX_REMINDERS = 5
MAX_REMINDER_MINUTES = 4 * 7 * 24 * 60
EVENT_ID = re.compile(r"[a-v0-9]{5,1024}\Z")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


class CalendarPayloadError(ValueError):
    pass


def reminders_for(minutes: list[int]) -> dict:
    if len(minutes) > MAX_REMINDERS:
        raise CalendarPayloadError("Google Calendar permits at most five reminders")
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        or not 0 <= value <= MAX_REMINDER_MINUTES
        for value in minutes
    ):
        raise CalendarPayloadError("Reminder minutes must be between 0 and 40320")
    if not minutes:
        return {"useDefault": True}
    return {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": value} for value in minutes],
    }


def recurrence_to_rrule(recurrence: dict) -> list[str]:
    frequency = recurrence.get("freq")
    if frequency not in {"daily", "weekly", "monthly", "yearly"}:
        raise CalendarPayloadError("Unsupported recurrence frequency")
    interval = recurrence.get("interval", 1)
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
        raise CalendarPayloadError("Recurrence interval must be positive")

    parts = [f"FREQ={frequency.upper()}", f"INTERVAL={interval}"]
    weekdays = recurrence.get("weekdays") or []
    if not isinstance(weekdays, list) or any(
        not isinstance(day, int) or isinstance(day, bool) or not 0 <= day <= 6
        for day in weekdays
    ):
        raise CalendarPayloadError("Recurrence weekdays must be integers from 0 through 6")
    if weekdays:
        parts.append("BYDAY=" + ",".join(WEEKDAYS[day] for day in weekdays))

    for key, label, minimum, maximum in (
        ("month", "BYMONTH", 1, 12),
        ("month_day", "BYMONTHDAY", 1, 31),
        ("position", "BYSETPOS", -5, 5),
        ("count", "COUNT", 1, None),
    ):
        value = recurrence.get(key)
        if value is None:
            continue
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < minimum
            or (maximum is not None and value > maximum)
            or (key == "position" and value == 0)
        ):
            raise CalendarPayloadError(f"Invalid recurrence {key}")
        parts.append(f"{label}={value}")

    until = recurrence.get("until")
    if until is not None:
        try:
            parts.append("UNTIL=" + date.fromisoformat(until).strftime("%Y%m%d"))
        except (TypeError, ValueError) as error:
            raise CalendarPayloadError("Invalid recurrence until date") from error
    return ["RRULE:" + ";".join(parts)]


def build_event(
    resolved: dict, event_id: str, timezone_name: str = "Europe/Chisinau"
) -> dict:
    if EVENT_ID.fullmatch(event_id) is None:
        raise CalendarPayloadError("Invalid Google Calendar event ID")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise CalendarPayloadError("Invalid calendar timezone") from error

    title = resolved.get("title")
    if not isinstance(title, str) or not title:
        raise CalendarPayloadError("Event title is required")
    event = {
        "id": event_id,
        "summary": title,
        "extendedProperties": {"private": {"voice_calendar_bot": "1"}},
    }
    recurrence = resolved.get("recurrence")
    if resolved.get("all_day"):
        try:
            start = date.fromisoformat(resolved["date"])
        except (KeyError, TypeError, ValueError) as error:
            raise CalendarPayloadError("All-day event date is required") from error
        event["start"] = {"date": start.isoformat()}
        event["end"] = {"date": (start + timedelta(days=1)).isoformat()}
        if recurrence:
            event["start"]["timeZone"] = timezone_name
            event["end"]["timeZone"] = timezone_name
    else:
        try:
            start = datetime.fromisoformat(resolved["start"])
            duration = resolved["duration_minutes"]
        except (KeyError, TypeError, ValueError) as error:
            raise CalendarPayloadError("Timed event start and duration are required") from error
        if start.tzinfo is None or not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise CalendarPayloadError("Timed event start and duration are required")
        end = (start.astimezone(timezone.utc) + timedelta(minutes=duration)).astimezone(zone)
        event["start"] = {"dateTime": start.isoformat(), "timeZone": timezone_name}
        event["end"] = {"dateTime": end.isoformat(), "timeZone": timezone_name}

    location = resolved.get("location")
    if location is not None:
        if not isinstance(location, str):
            raise CalendarPayloadError("Event location must be text")
        event["location"] = location
    if recurrence:
        if not isinstance(recurrence, dict):
            raise CalendarPayloadError("Event recurrence must be an object")
        event["recurrence"] = recurrence_to_rrule(recurrence)
    event["reminders"] = reminders_for(resolved.get("reminders_minutes", []))
    return event
