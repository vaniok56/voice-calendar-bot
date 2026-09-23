import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from cryptography.fernet import Fernet


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    elevenlabs_api_key: str
    elevenlabs_model: str
    deepseek_api_key: str
    extraction_model: str
    extraction_timeout: int
    data_dir: Path
    log_dir: Path
    log_level: str
    log_retention_days: int
    voice_retention_hours: int
    voice_cleanup_interval_seconds: int
    debug: bool
    calendar_write_enabled: bool
    google_oauth_client_id: str
    google_oauth_client_secret: str
    google_oauth_redirect_uri: str
    calendar_token_encryption_key: str
    calendar_callback_port: int
    calendar_timezone: str


def _int_env(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error


def _bool_env(name: str, default: str = "false") -> bool:
    value = os.environ.get(name, default).strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name} must be true or false")
    return value == "true"


def load_config() -> Config:
    load_dotenv()
    token = os.environ.get("BOT_TOKEN", "").strip()
    owner_id = os.environ.get("OWNER_ID", "").strip()
    if not token or not owner_id:
        raise RuntimeError("BOT_TOKEN and OWNER_ID must be set in .env")
    try:
        owner_id = int(owner_id)
    except ValueError as error:
        raise RuntimeError("OWNER_ID must be a positive integer") from error
    if owner_id <= 0:
        raise RuntimeError("OWNER_ID must be a positive integer")

    elevenlabs_key = os.environ.get("ELEVENLABS_API", "").strip()
    if not elevenlabs_key:
        raise RuntimeError("ELEVENLABS_API must be set in .env")
    elevenlabs_model = os.environ.get("ELEVENLABS_MODEL", "scribe_v2").strip()
    if not elevenlabs_model:
        raise RuntimeError("ELEVENLABS_MODEL must not be empty")

    deepseek_key = os.environ.get("DEEPSEEK_API", "").strip()
    if not deepseek_key:
        raise RuntimeError("DEEPSEEK_API must be set in .env")
    extraction_model = os.environ.get("EXTRACTION_MODEL", "deepseek-flash").strip()
    if not extraction_model:
        raise RuntimeError("EXTRACTION_MODEL must not be empty")
    extraction_timeout = _int_env("EXTRACTION_TIMEOUT", "60")
    if extraction_timeout <= 0:
        raise RuntimeError("EXTRACTION_TIMEOUT must be positive")

    retention_days = _int_env("LOG_RETENTION_DAYS", "7")
    if retention_days <= 0:
        raise RuntimeError("LOG_RETENTION_DAYS must be positive")
    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise RuntimeError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

    voice_retention_hours = _int_env("VOICE_RETENTION_HOURS", "168")
    if voice_retention_hours <= 0:
        raise RuntimeError("VOICE_RETENTION_HOURS must be positive")
    voice_cleanup_interval_seconds = _int_env("VOICE_CLEANUP_INTERVAL_SECONDS", "3600")
    if voice_cleanup_interval_seconds <= 0:
        raise RuntimeError("VOICE_CLEANUP_INTERVAL_SECONDS must be positive")

    debug = _bool_env("DEBUG")
    calendar_write_enabled = _bool_env("CALENDAR_WRITE_ENABLED")
    calendar_callback_port = _int_env("CALENDAR_CALLBACK_PORT", "8080")
    if not 1 <= calendar_callback_port <= 65535:
        raise RuntimeError("CALENDAR_CALLBACK_PORT must be between 1 and 65535")
    calendar_timezone = os.environ.get("CALENDAR_TIMEZONE", "Europe/Chisinau").strip()
    try:
        ZoneInfo(calendar_timezone)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError("CALENDAR_TIMEZONE must be an IANA timezone") from error
    google_oauth_client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    google_oauth_client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    google_oauth_redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    calendar_token_encryption_key = os.environ.get("CALENDAR_TOKEN_ENCRYPTION_KEY", "").strip()
    if calendar_write_enabled and not all((
        google_oauth_client_id, google_oauth_client_secret, google_oauth_redirect_uri,
    )):
        raise RuntimeError("Google OAuth configuration is required when CALENDAR_WRITE_ENABLED=true")
    if all((google_oauth_client_id, google_oauth_client_secret, google_oauth_redirect_uri)):
        if not calendar_token_encryption_key:
            raise RuntimeError("CALENDAR_TOKEN_ENCRYPTION_KEY is required with Google OAuth")
        try:
            Fernet(calendar_token_encryption_key.encode())
        except ValueError as error:
            raise RuntimeError("CALENDAR_TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from error

    return Config(
        bot_token=token,
        owner_id=owner_id,
        elevenlabs_api_key=elevenlabs_key,
        elevenlabs_model=elevenlabs_model,
        deepseek_api_key=deepseek_key,
        extraction_model=extraction_model,
        extraction_timeout=extraction_timeout,
        data_dir=Path(os.environ.get("DATA_DIR", "data")).expanduser().resolve(),
        log_dir=Path(os.environ.get("LOG_DIR", "logs")).expanduser().resolve(),
        log_level=log_level,
        log_retention_days=retention_days,
        voice_retention_hours=voice_retention_hours,
        voice_cleanup_interval_seconds=voice_cleanup_interval_seconds,
        debug=debug,
        calendar_write_enabled=calendar_write_enabled,
        google_oauth_client_id=google_oauth_client_id,
        google_oauth_client_secret=google_oauth_client_secret,
        google_oauth_redirect_uri=google_oauth_redirect_uri,
        calendar_token_encryption_key=calendar_token_encryption_key,
        calendar_callback_port=calendar_callback_port,
        calendar_timezone=calendar_timezone,
    )
