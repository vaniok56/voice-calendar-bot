import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
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


def remove_expired_voices(voice_dir: Path) -> int:
    if not voice_dir.exists():
        return 0
    cutoff = time.time() - RETENTION_SECONDS
    removed = 0
    for path in voice_dir.glob("*.ogg"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            pass
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
                    f"Length: {duration}"
                )
            except Exception as error:
                log.warning("Voice receipt failed (%s)", type(error).__name__)
        finally:
            voice_queue.task_done()


async def cleanup_loop(voice_dir: Path) -> None:
    while True:
        removed = remove_expired_voices(voice_dir)
        if removed:
            log.info("Removed %s expired voice message(s)", removed)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
