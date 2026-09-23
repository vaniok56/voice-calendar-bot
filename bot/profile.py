"""Private per-user Calendar preferences; invalid profiles cannot authorize writes."""

import json
import secrets
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .calendar import _calendar_user_path, _write_private_json
from .semantic import DEFAULT_DURATION, EVENT_TYPES


class ProfileError(ValueError):
    pass


class ProfileConflict(ProfileError):
    pass


def _path(data_dir: Path, user_id: int) -> Path:
    return _calendar_user_path(data_dir, "profiles", user_id)


def _integer(value, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def label_key(label: str) -> str:
    return unicodedata.normalize("NFC", label.strip()).casefold()


def clean_label(label: str) -> str:
    if not isinstance(label, str):
        raise ProfileError("Type name must be text")
    label = unicodedata.normalize("NFC", label.strip())
    if not 1 <= len(label) <= 40 or not all(
        char.isalpha() or char.isdigit() or char in " -" for char in label
    ) or label_key(label) in {label_key(name) for name in EVENT_TYPES}:
        raise ProfileError("Use 1–40 letters, numbers, spaces or hyphens; built-in names are reserved")
    return label


def validate(profile: dict) -> None:
    try:
        if not isinstance(profile, dict) or set(profile) != {
            "version", "revision", "timezone", "auto_write_enabled", "type_durations", "custom_types"
        } or type(profile["version"]) is not int or profile["version"] != 1 or not _integer(profile["revision"], 1, 2**63 - 1):
            raise ValueError
        if not isinstance(profile["timezone"], str) or not profile["timezone"]:
            raise ValueError
        ZoneInfo(profile["timezone"])
        if type(profile["auto_write_enabled"]) is not bool:
            raise ValueError
        durations = profile["type_durations"]
        if not isinstance(durations, dict) or any(
            name not in DEFAULT_DURATION or not _integer(minutes, 1, 1440)
            for name, minutes in durations.items()
        ):
            raise ValueError
        customs = profile["custom_types"]
        if not isinstance(customs, list) or len(customs) > 20:
            raise ValueError
        names, ids = set(), set()
        for entry in customs:
            if not isinstance(entry, dict) or set(entry) != {"id", "type", "duration_minutes"}:
                raise ValueError
            name = clean_label(entry["type"])
            if name != entry["type"] or not isinstance(entry["id"], str) or not 8 <= len(entry["id"]) <= 64 or not all(
                char.isascii() and (char.isalnum() or char in "_-") for char in entry["id"]
            ) or not _integer(entry["duration_minutes"], 1, 1440):
                raise ValueError
            key = label_key(name)
            if key in names or entry["id"] in ids:
                raise ValueError
            names.add(key)
            ids.add(entry["id"])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as error:
        raise ProfileError("Calendar profile needs repair; automatic writes are disabled") from error


def load_profile(data_dir: Path, user_id: int, default_timezone: str) -> dict:
    path = _path(data_dir, user_id)
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        profile = {
            "version": 1, "revision": 1, "timezone": default_timezone,
            "auto_write_enabled": False, "type_durations": {}, "custom_types": [],
        }
        validate(profile)
        _write_private_json(path, profile)
        return profile
    except (OSError, ValueError) as error:
        raise ProfileError("Calendar profile needs repair; automatic writes are disabled") from error
    validate(profile)
    return profile


def save_profile(data_dir: Path, user_id: int, default_timezone: str, profile: dict) -> dict:
    current = load_profile(data_dir, user_id, default_timezone)
    if profile.get("revision") != current["revision"]:
        raise ProfileConflict("Settings changed; reload and try again")
    updated = {**profile, "revision": current["revision"] + 1}
    validate(updated)
    _write_private_json(_path(data_dir, user_id), updated)
    return updated


def new_custom_type(label: str, duration_minutes: int) -> dict:
    return {"id": secrets.token_urlsafe(12), "type": clean_label(label), "duration_minutes": duration_minutes}
