import asyncio
import contextlib
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from .logging_config import CHISINAU


log = logging.getLogger(__name__)


def cleanup_expired(voice_root: Path) -> int:
    if not voice_root.is_dir():
        return 0

    now = datetime.now(CHISINAU)
    removed = 0
    for record_path in sorted(voice_root.glob("*/*/record.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            expired = datetime.fromisoformat(record["expires_at"]) <= now
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("Voice retention: skipping malformed %s", record_path)
            continue
        if not expired:
            continue

        shutil.rmtree(record_path.parent, ignore_errors=True)
        with contextlib.suppress(OSError):
            record_path.parent.parent.rmdir()
        removed += 1

    return removed


async def retention_loop(voice_root: Path, interval_seconds: int) -> None:
    while True:
        try:
            removed = cleanup_expired(voice_root)
            if removed:
                log.info("Removed %s expired voice record(s)", removed)
        except Exception:
            log.exception("Voice retention cleanup failed")
        await asyncio.sleep(interval_seconds)
