import base64
import hashlib
import json
import re
import secrets
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
MAX_REMINDERS = 5
MAX_REMINDER_MINUTES = 4 * 7 * 24 * 60
EVENT_ID = re.compile(r"[a-v0-9]{5,1024}\Z")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
STATE_TTL = timedelta(minutes=10)
WRITE_ID = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=5)
ACTIVE_WRITES: set[str] = set()


class CalendarPayloadError(ValueError):
    pass


class CalendarAuthError(ValueError):
    pass


class CalendarAPIError(RuntimeError):
    pass


def oauth_is_configured(config) -> bool:
    return all((
        config.google_oauth_client_id,
        config.google_oauth_client_secret,
        config.google_oauth_redirect_uri,
    ))


def _calendar_root(data_dir: Path) -> Path:
    root = data_dir / "google-calendar"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def _write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _state_path(data_dir: Path, state: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", state):
        raise CalendarAuthError("Invalid OAuth state")
    return _calendar_root(data_dir) / "states" / f"{state}.json"


def _clear_user_states(data_dir: Path, user_id: int) -> None:
    states = _calendar_root(data_dir) / "states"
    for path in states.glob("*.json"):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("telegram_user_id") == user_id:
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)


def _prune_expired_states(data_dir: Path, now: datetime) -> None:
    states = _calendar_root(data_dir) / "states"
    for path in states.glob("*.json"):
        try:
            expires_at = datetime.fromisoformat(
                json.loads(path.read_text(encoding="utf-8"))["expires_at"]
            )
            if expires_at.tzinfo is None or expires_at <= now:
                path.unlink(missing_ok=True)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            path.unlink(missing_ok=True)


def _token_path(data_dir: Path, user_id: int) -> Path:
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise CalendarAuthError("Invalid Telegram user")
    return _calendar_root(data_dir) / "tokens" / f"{user_id}.json"


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def create_authorization_url(config, user_id: int, *, now: datetime | None = None) -> str:
    if not oauth_is_configured(config):
        raise CalendarAuthError("Google OAuth is not configured")
    now = now or datetime.now(timezone.utc)
    _prune_expired_states(config.data_dir, now)
    _clear_user_states(config.data_dir, user_id)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    _write_private_json(_state_path(config.data_dir, state), {
        "telegram_user_id": user_id,
        "code_verifier": verifier,
        "expires_at": (now + STATE_TTL).isoformat(),
    })
    return OAUTH_AUTHORIZE_URL + "?" + urlencode({
        "client_id": config.google_oauth_client_id,
        "redirect_uri": config.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": CALENDAR_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
    })


def consume_oauth_state(data_dir: Path, state: str, *, now: datetime | None = None) -> dict:
    path = _state_path(data_dir, state)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalendarAuthError("OAuth state is invalid or already used") from error
    path.unlink(missing_ok=True)
    try:
        user_id = value["telegram_user_id"]
        verifier = value["code_verifier"]
        expires_at = datetime.fromisoformat(value["expires_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise CalendarAuthError("OAuth state is invalid") from error
    now = now or datetime.now(timezone.utc)
    if (
        not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0
        or not isinstance(verifier, str) or not verifier
        or expires_at.tzinfo is None or expires_at <= now
    ):
        raise CalendarAuthError("OAuth state is expired or invalid")
    return {"telegram_user_id": user_id, "code_verifier": verifier}


async def exchange_code(config, code: str, verifier: str) -> dict:
    if not code or not verifier:
        raise CalendarAuthError("OAuth code exchange failed")
    data = {
        "client_id": config.google_oauth_client_id,
        "client_secret": config.google_oauth_client_secret,
        "redirect_uri": config.google_oauth_redirect_uri,
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
    }
    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.post(OAUTH_TOKEN_URL, data=data) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    raise CalendarAuthError("OAuth code exchange failed")
    except (aiohttp.ClientError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CalendarAuthError("OAuth code exchange failed") from error
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
        raise CalendarAuthError("OAuth code exchange failed")
    scopes = set(str(body.get("scope", "")).split())
    if CALENDAR_SCOPE not in scopes:
        raise CalendarAuthError("Google Calendar permission was not granted")
    refresh_token = body.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise CalendarAuthError("Google did not provide a refresh token")
    expires_in = body.get("expires_in")
    if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
        raise CalendarAuthError("OAuth token expiry is invalid")
    return {
        "access_token": body["access_token"],
        "refresh_token": refresh_token,
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
        "scope": sorted(scopes),
    }


def save_token(data_dir: Path, user_id: int, token: dict) -> None:
    _write_private_json(_token_path(data_dir, user_id), token)


def load_token(data_dir: Path, user_id: int) -> dict | None:
    path = _token_path(data_dir, user_id)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalendarAuthError("Stored Google Calendar token is invalid") from error
    if not isinstance(value, dict):
        raise CalendarAuthError("Stored Google Calendar token is invalid")
    return value


def disconnect(data_dir: Path, user_id: int) -> bool:
    _clear_user_states(data_dir, user_id)
    path = _token_path(data_dir, user_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def _write_path(data_dir: Path, write_id: str) -> Path:
    if WRITE_ID.fullmatch(write_id) is None:
        raise CalendarPayloadError("Invalid calendar write ID")
    return _calendar_root(data_dir) / "writes" / f"{write_id}.json"


def new_google_event_id() -> str:
    return "evt" + "".join(secrets.choice("0123456789abcdefghijklmnopqrstuv") for _ in range(29))


def create_write(
    data_dir: Path, user_id: int, source_record: str, payload: dict
) -> dict:
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise CalendarPayloadError("Invalid Telegram user")
    if not isinstance(source_record, str) or not source_record:
        raise CalendarPayloadError("Calendar write source record is required")
    if not isinstance(payload, dict):
        raise CalendarPayloadError("Calendar event payload is invalid")
    event_id = payload.get("id")
    if EVENT_ID.fullmatch(event_id or "") is None:
        raise CalendarPayloadError("Calendar event payload is invalid")
    write_id = secrets.token_urlsafe(18)
    record = {
        "write_id": write_id,
        "telegram_user_id": user_id,
        "source_record": source_record,
        "event_id": event_id,
        "payload_fingerprint": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "payload": payload,
        "status": "pending",
        "google_event_id": None,
        "google_html_link": None,
        "error": None,
    }
    _write_private_json(_write_path(data_dir, write_id), record)
    return record


def load_write(data_dir: Path, write_id: str) -> dict | None:
    path = _write_path(data_dir, write_id)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalendarPayloadError("Stored calendar write is invalid") from error
    if not isinstance(record, dict) or record.get("write_id") != write_id:
        raise CalendarPayloadError("Stored calendar write is invalid")
    return record


def update_write(data_dir: Path, write_id: str, **updates) -> dict:
    record = load_write(data_dir, write_id)
    if record is None:
        raise CalendarPayloadError("Calendar write was not found")
    record.update(updates)
    _write_private_json(_write_path(data_dir, write_id), record)
    return record


def claim_write(data_dir: Path, write_id: str) -> dict | None:
    record = load_write(data_dir, write_id)
    if record is None or record.get("status") not in {"pending", "creating", "failed"}:
        return None
    if write_id in ACTIVE_WRITES:
        return None
    ACTIVE_WRITES.add(write_id)
    return update_write(data_dir, write_id, status="creating", error=None)


def release_write(write_id: str) -> None:
    ACTIVE_WRITES.discard(write_id)


def replace_write_payload(data_dir: Path, write_id: str, payload: dict) -> dict:
    record = load_write(data_dir, write_id)
    if record is None or record.get("status") != "pending":
        raise CalendarPayloadError("Calendar write can no longer be edited")
    if not isinstance(payload, dict) or payload.get("id") != record.get("event_id"):
        raise CalendarPayloadError("Calendar event payload is invalid")
    return update_write(
        data_dir,
        write_id,
        payload=payload,
        payload_fingerprint=hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )


def edit_payload(payload: dict, field: str, value: str, timezone_name: str) -> dict:
    event = deepcopy(payload)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise CalendarPayloadError("Invalid calendar timezone") from error
    if field == "title":
        if not value.strip():
            raise CalendarPayloadError("Title must not be empty")
        event["summary"] = value.strip()
        return event
    if field == "date":
        try:
            day = date.fromisoformat(value.strip())
        except ValueError as error:
            raise CalendarPayloadError("Use YYYY-MM-DD") from error
        if "date" in event.get("start", {}):
            event["start"]["date"] = day.isoformat()
            event["end"]["date"] = (day + timedelta(days=1)).isoformat()
            return event
        try:
            start = datetime.fromisoformat(event["start"]["dateTime"])
            end = datetime.fromisoformat(event["end"]["dateTime"])
        except (KeyError, TypeError, ValueError) as error:
            raise CalendarPayloadError("Calendar event payload is invalid") from error
        duration = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
        start = datetime.combine(day, start.timetz().replace(tzinfo=None), zone)
        end = (start.astimezone(timezone.utc) + duration).astimezone(zone)
        event["start"]["dateTime"] = start.isoformat()
        event["end"]["dateTime"] = end.isoformat()
        return event
    if field == "time":
        if "date" in event.get("start", {}):
            raise CalendarPayloadError("All-day event time cannot be edited")
        try:
            hour, minute = map(int, value.strip().split(":"))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59 or len(value.strip()) != 5:
                raise ValueError
            start = datetime.fromisoformat(event["start"]["dateTime"])
            end = datetime.fromisoformat(event["end"]["dateTime"])
        except (KeyError, TypeError, ValueError) as error:
            raise CalendarPayloadError("Use HH:MM (24-hour time)") from error
        duration = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
        start = datetime.combine(start.date(), datetime.min.time(), zone).replace(
            hour=hour, minute=minute
        )
        end = (start.astimezone(timezone.utc) + duration).astimezone(zone)
        event["start"]["dateTime"] = start.isoformat()
        event["end"]["dateTime"] = end.isoformat()
        return event
    raise CalendarPayloadError("Unsupported calendar edit field")


def _token_is_expired(token: dict, *, now: datetime | None = None) -> bool:
    try:
        expires_at = datetime.fromisoformat(token["expires_at"])
    except (KeyError, TypeError, ValueError):
        return True
    if expires_at.tzinfo is None:
        return True
    now = now or datetime.now(timezone.utc)
    return expires_at <= now + timedelta(seconds=60)


async def refresh_access_token(config, token: dict) -> dict:
    refresh_token = token.get("refresh_token") if isinstance(token, dict) else None
    if not isinstance(refresh_token, str) or not refresh_token:
        raise CalendarAuthError("Stored Google Calendar token is invalid")
    data = {
        "client_id": config.google_oauth_client_id,
        "client_secret": config.google_oauth_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OAUTH_TOKEN_URL, data=data) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    raise CalendarAuthError("Google Calendar connection must be reconnected")
    except aiohttp.ClientError as error:
        raise CalendarAuthError("Google Calendar connection must be reconnected") from error
    expires_in = body.get("expires_in") if isinstance(body, dict) else None
    access_token = body.get("access_token") if isinstance(body, dict) else None
    if (
        not isinstance(access_token, str) or not access_token
        or not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0
    ):
        raise CalendarAuthError("Google Calendar token refresh failed")
    refreshed = {**token, "access_token": access_token}
    refreshed["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()
    return refreshed


async def insert_event(token: dict, payload: dict) -> dict:
    access_token = token.get("access_token") if isinstance(token, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise CalendarAuthError("Stored Google Calendar token is invalid")
    event_id = payload.get("id") if isinstance(payload, dict) else None
    if EVENT_ID.fullmatch(event_id or "") is None:
        raise CalendarPayloadError("Invalid Google Calendar event payload")
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.post(EVENTS_URL, headers=headers, json=payload) as response:
                body = await response.json(content_type=None)
                if response.status == 409:
                    async with session.get(f"{EVENTS_URL}/{event_id}", headers=headers) as existing:
                        body = await existing.json(content_type=None)
                        if existing.status == 200 and (
                            isinstance(body, dict)
                            and body.get("id") == event_id
                            and body.get("extendedProperties", {}).get("private", {}).get("voice_calendar_bot") == "1"
                        ):
                            return body
                    raise CalendarAPIError("Google Calendar event conflict")
                if response.status not in {200, 201} or not isinstance(body, dict):
                    raise CalendarAPIError("Google Calendar event creation failed")
    except (aiohttp.ClientError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CalendarAPIError("Google Calendar event creation failed") from error
    return body


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


def recurrence_to_rrule(
    recurrence: dict, *, timed: bool = False, start: datetime | None = None
) -> list[str]:
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
    if until is not None and recurrence.get("count") is not None:
        raise CalendarPayloadError("Recurrence cannot have both count and until")
    if until is not None:
        try:
            until_day = date.fromisoformat(until)
            if timed:
                if start is None or start.tzinfo is None:
                    raise ValueError
                until_value = datetime.combine(
                    until_day, datetime.max.time(), start.tzinfo
                ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            else:
                until_value = until_day.strftime("%Y%m%d")
            parts.append("UNTIL=" + until_value)
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
    timed_start = None
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
        timed_start = start
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
        event["recurrence"] = recurrence_to_rrule(
            recurrence, timed=timed_start is not None, start=timed_start
        )
    event["reminders"] = reminders_for(resolved.get("reminders_minutes", []))
    return event
