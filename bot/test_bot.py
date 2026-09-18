import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from .asr import (
    ASRServiceError,
    TranscriptionResponse,
    format_duration,
    transcribe_voice,
)
from .config import Config, load_config


class TestASR(unittest.TestCase):

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0:00 (0s)")
        self.assertEqual(format_duration(14), "0:14 (14s)")
        self.assertEqual(format_duration(65), "1:05 (65s)")
        self.assertEqual(format_duration(3600), "60:00 (3600s)")

    @patch("bot.asr.urlopen")
    def test_transcribe_voice_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"text": "Meeting at 3 PM", "language_code": "eng"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with tempfile.NamedTemporaryFile(suffix=".ogg") as tmp:
            tmp.write(b"dummy audio data")
            tmp.flush()
            import asyncio
            result = asyncio.run(transcribe_voice(Path(tmp.name), api_key="dummy_key"))

        self.assertIsInstance(result, TranscriptionResponse)
        self.assertEqual(result.text, "Meeting at 3 PM")
        self.assertEqual(result.language_code, "eng")
        self.assertGreaterEqual(result.wait_time_seconds, 0)

    @patch("bot.asr.urlopen")
    def test_transcribe_voice_empty_text_error(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"text": "", "language_code": "eng"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with tempfile.NamedTemporaryFile(suffix=".ogg") as tmp:
            tmp.write(b"dummy audio data")
            tmp.flush()
            import asyncio
            with self.assertRaises(ASRServiceError) as ctx:
                asyncio.run(transcribe_voice(Path(tmp.name), api_key="dummy_key"))
            self.assertIn("empty transcript", str(ctx.exception))


class TestConfig(unittest.TestCase):
    @patch.dict(os.environ, {
        "BOT_TOKEN": "123:ABC",
        "OWNER_ID": "999",
        "ELEVENLABS_API": "test_eleven_key",
    }, clear=True)
    def test_load_config_success(self):
        cfg = load_config()
        self.assertEqual(cfg.bot_token, "123:ABC")
        self.assertEqual(cfg.owner_id, 999)
        self.assertEqual(cfg.elevenlabs_api_key, "test_eleven_key")


if __name__ == "__main__":
    unittest.main()
