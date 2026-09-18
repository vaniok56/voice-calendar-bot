import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    elevenlabs_api_key: str
    data_dir: Path
    log_dir: Path
    log_level: str
    log_retention_days: int
    voice_retention_hours: int
    voice_cleanup_interval_seconds: int


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

    retention_days = int(os.environ.get("LOG_RETENTION_DAYS", "7"))
    if retention_days <= 0:
        raise RuntimeError("LOG_RETENTION_DAYS must be positive")
    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise RuntimeError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

    voice_retention_hours = int(os.environ.get("VOICE_RETENTION_HOURS", "168"))
    if voice_retention_hours <= 0:
        raise RuntimeError("VOICE_RETENTION_HOURS must be positive")
    voice_cleanup_interval_seconds = int(os.environ.get("VOICE_CLEANUP_INTERVAL_SECONDS", "3600"))
    if voice_cleanup_interval_seconds <= 0:
        raise RuntimeError("VOICE_CLEANUP_INTERVAL_SECONDS must be positive")

    return Config(
        bot_token=token,
        owner_id=owner_id,
        elevenlabs_api_key=elevenlabs_key,
        data_dir=Path(os.environ.get("DATA_DIR", "data")).expanduser().resolve(),
        log_dir=Path(os.environ.get("LOG_DIR", "logs")).expanduser().resolve(),
        log_level=log_level,
        log_retention_days=retention_days,
        voice_retention_hours=voice_retention_hours,
        voice_cleanup_interval_seconds=voice_cleanup_interval_seconds,
    )
