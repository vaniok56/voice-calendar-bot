import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


CHISINAU = ZoneInfo("Europe/Chisinau")


class ChisinauFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        timestamp = datetime.fromtimestamp(record.created, CHISINAU)
        return timestamp.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


class ColoredFormatter(ChisinauFormatter):
    COLORS = {
        "DEBUG": "\033[94m",
        "INFO": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m",
        "CRITICAL": "\033[1;91m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        original = record.levelname
        color = self.COLORS.get(original, self.RESET)
        record.levelname = f"{color}[{original}]{self.RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = original


class DailyFileHandler(logging.FileHandler):
    def __init__(self, log_dir: Path, retention_days: int) -> None:
        self.log_dir = log_dir
        self.retention_days = retention_days
        self.current_date = datetime.now(CHISINAU).strftime("%d_%m_%y")
        super().__init__(self._path_for(self.current_date), encoding="utf-8", delay=True)

    def _path_for(self, date: str) -> Path:
        return self.log_dir / f"bot_{date}.log"

    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        self._remove_expired_files()
        return stream

    def emit(self, record: logging.LogRecord) -> None:
        record_date = datetime.fromtimestamp(record.created, CHISINAU).strftime("%d_%m_%y")
        if record_date != self.current_date:
            if self.stream:
                self.stream.close()
            self.stream = None
            self.current_date = record_date
            self.baseFilename = os.path.abspath(self._path_for(record_date))
        super().emit(record)

    def _remove_expired_files(self) -> None:
        files = sorted(
            self.log_dir.glob("bot_*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in files[self.retention_days:]:
            path.unlink(missing_ok=True)


def _migrate_legacy_log(log_dir: Path) -> None:
    legacy = log_dir / "bot.log"
    if not legacy.exists():
        return
    date = datetime.fromtimestamp(legacy.stat().st_mtime, CHISINAU).strftime("%d_%m_%y")
    destination = log_dir / f"bot_{date}.log"
    if destination.exists():
        with destination.open("ab") as output, legacy.open("rb") as source:
            shutil.copyfileobj(source, output)
        legacy.unlink()
    else:
        legacy.replace(destination)
    destination.chmod(0o600)


def configure_logging(log_dir: Path, level: str, retention_days: int) -> None:
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_dir.chmod(0o700)
    _migrate_legacy_log(log_dir)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter(
        "%(asctime)s.%(msecs)03d | %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    file_handler = DailyFileHandler(log_dir, retention_days)
    file_handler.setFormatter(ChisinauFormatter(
        "%(asctime)s.%(msecs)03d | [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))

    logging.basicConfig(
        level=getattr(logging, level),
        handlers=[console_handler, file_handler],
        force=True,
    )
    logging.captureWarnings(True)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
