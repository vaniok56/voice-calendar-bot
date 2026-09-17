import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from html import escape
from itertools import count
from pathlib import Path

from aiogram import F, Bot, Router
from aiogram.types import Message

from ..logging_config import CHISINAU


router = Router(name="voice")
log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 2 * 1024 * 1024
RETENTION_SECONDS = 7 * 24 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 60 * 60
VoiceJob = tuple[Message, Bot]


def reserve_voice_path(voice_dir: Path) -> Path:
    voice_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    voice_dir.chmod(0o700)
    timestamp = datetime.now(CHISINAU).strftime("%Y-%m-%d_%H-%M-%S")
    for index in count():
        suffix = f"_{index}" if index else ""
        path = voice_dir / f"{timestamp}{suffix}.ogg"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        return path


def write_metadata(path: Path, metadata: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def read_metadata(path: Path) -> dict | None:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return metadata if isinstance(metadata, dict) else None


def pending_metadata(voice_dir: Path, user_id: int) -> Path | None:
    for path in sorted(voice_dir.glob("*.json")):
        metadata = read_metadata(path)
        if metadata and metadata.get("telegram_user_id") == user_id and not metadata.get("corpus_id"):
            return path
    return None


def corpus_id_exists(voice_dir: Path, corpus_id: str) -> bool:
    return any(
        metadata and metadata.get("corpus_id") == corpus_id
        for metadata in (read_metadata(path) for path in voice_dir.glob("*.json"))
    )


def remove_expired_voices(voice_dir: Path) -> int:
    if not voice_dir.exists():
        return 0
    cutoff = time.time() - RETENTION_SECONDS
    removed = 0
    for path in voice_dir.glob("*.ogg"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                path.with_suffix(".json").unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            pass
    for path in voice_dir.glob("*.json"):
        if not path.with_suffix(".ogg").exists():
            path.unlink(missing_ok=True)
    return removed


@router.message(F.voice)
async def receive_voice(message: Message, bot: Bot, voice_queue: asyncio.Queue[VoiceJob]) -> None:
    voice = message.voice
    if voice is None or voice.file_size is None or voice.file_size <= 0:
        await message.answer("Voice message has no usable size information.")
        return
    if voice.file_size > MAX_VOICE_BYTES:
        await message.answer("Voice message exceeds the 2 MiB limit.")
        return
    await voice_queue.put((message, bot))


async def download_worker(voice_queue: asyncio.Queue[VoiceJob], voice_dir: Path) -> None:
    while True:
        message, bot = await voice_queue.get()
        destination: Path | None = None
        try:
            voice = message.voice
            if voice is None:
                continue
            destination = reserve_voice_path(voice_dir)
            await bot.download(voice, destination=destination)
            destination.chmod(0o600)
            size = destination.stat().st_size
            duration = str(timedelta(seconds=voice.duration))
            user_id = message.from_user.id if message.from_user else 0
            write_metadata(destination.with_suffix(".json"), {
                "file": destination.name,
                "telegram_user_id": user_id,
                "stored_at": datetime.now(CHISINAU).isoformat(timespec="seconds"),
                "size_bytes": size,
                "duration_seconds": voice.duration,
                "corpus_id": None,
                "notes": None,
            })
            log.info(
                "%s - voice stored file=%s size_bytes=%s duration_seconds=%s",
                user_id,
                destination.name,
                size,
                voice.duration,
            )
        except Exception as error:
            if destination:
                destination.unlink(missing_ok=True)
                destination.with_suffix(".json").unlink(missing_ok=True)
            log.error("Voice download failed (%s)", type(error).__name__)
            try:
                await message.answer("Could not store voice message. Please try again.")
            except Exception:
                pass
        else:
            try:
                await message.answer(
                    "Voice message stored.\n"
                    f"File: <code>{destination.name}</code>\n"
                    f"Size: {size / 1024 / 1024:.2f} MiB ({size:,} bytes)\n"
                    f"Length: {duration}\n\n"
                    "Reply with the corpus ID and notes, for example:\n"
                    "<code>RO-01 calm, normal speed, quiet room</code>"
                )
            except Exception as error:
                log.warning("Voice receipt failed (%s)", type(error).__name__)
        finally:
            voice_queue.task_done()


@router.message(F.text)
async def receive_comment(message: Message, voice_dir: Path) -> None:
    if not message.from_user or not message.text or message.text.startswith("/"):
        return
    metadata_path = pending_metadata(voice_dir, message.from_user.id)
    if metadata_path is None:
        return

    parts = message.text.strip().split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Use: <code>RO-01 your notes</code>")
        return
    corpus_id, notes = parts[0].upper(), parts[1].strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,31}", corpus_id) or not notes:
        await message.answer("Use a short ID followed by notes, for example: <code>RO-01 calm, quiet</code>")
        return
    if corpus_id_exists(voice_dir, corpus_id):
        await message.answer(f"ID <code>{corpus_id}</code> is already used.")
        return

    metadata = read_metadata(metadata_path)
    if metadata is None:
        await message.answer("Could not save notes for this recording.")
        return
    metadata["corpus_id"] = corpus_id
    metadata["notes"] = notes
    write_metadata(metadata_path, metadata)
    log.info(
        "%s - voice comment stored file=%s corpus_id=%s",
        message.from_user.id,
        metadata.get("file"),
        corpus_id,
    )
    await message.answer(
        f"Notes saved for <code>{metadata.get('file')}</code>.\n"
        f"ID: <code>{corpus_id}</code>\n"
        f"Notes: {escape(notes)}"
    )


async def cleanup_loop(voice_dir: Path) -> None:
    while True:
        removed = remove_expired_voices(voice_dir)
        if removed:
            log.info("Removed %s expired voice message(s)", removed)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
