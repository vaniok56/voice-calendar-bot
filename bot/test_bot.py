import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from .asr import (
    ASRServiceError,
    TranscriptionResponse,
    format_duration,
    transcribe_voice,
)
from .config import Config, load_config
from .extraction import ExtractionServiceError, _parse_content, extract_event
from .handlers.voice import format_resolved
from .resolver import resolve


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
        "MISTRAL_API": "test_mistral_key",
    }, clear=True)
    def test_load_config_success(self):
        cfg = load_config()
        self.assertEqual(cfg.bot_token, "123:ABC")
        self.assertEqual(cfg.owner_id, 999)
        self.assertEqual(cfg.elevenlabs_api_key, "test_eleven_key")
        self.assertEqual(cfg.mistral_api_key, "test_mistral_key")
        self.assertEqual(cfg.extraction_model, "mistral-large-latest")
        self.assertEqual(cfg.extraction_timeout, 60)

    @patch("bot.config.load_dotenv")
    @patch.dict(os.environ, {
        "BOT_TOKEN": "123:ABC",
        "OWNER_ID": "999",
        "ELEVENLABS_API": "test_eleven_key",
    }, clear=True)
    def test_load_config_requires_mistral(self, _mock_dotenv):
        with self.assertRaises(RuntimeError):
            load_config()


class TestExtraction(unittest.TestCase):
    def test_parse_content_fenced(self):
        raw = '```json\n{"operation": "create"}\n```'
        self.assertEqual(_parse_content(raw), {"operation": "create"})

    def test_parse_content_invalid(self):
        with self.assertRaises(ExtractionServiceError):
            _parse_content("not json")
        with self.assertRaises(ExtractionServiceError):
            _parse_content("[1, 2, 3]")

    @patch("bot.extraction.urllib.request.urlopen")
    def test_extract_event_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"operation": "create", "event_type": "class"}'}}]
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        import asyncio
        result = asyncio.run(extract_event("lab maine la 10", api_key="k", model="m"))
        self.assertEqual(result.raw["operation"], "create")
        self.assertGreaterEqual(result.wait_time_seconds, 0)

    @patch("bot.extraction.urllib.request.urlopen")
    def test_extract_event_retries_truncated_json(self, mock_urlopen):
        def response(content):
            body = MagicMock()
            body.read.return_value = json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode()
            context = MagicMock()
            context.__enter__.return_value = body
            return context

        mock_urlopen.side_effect = [
            response('{"operation": "create", "event_type": "call", "title": "x"'),
            response('{"operation": "create", "event_type": "call"}'),
        ]
        import asyncio
        result = asyncio.run(extract_event("call", api_key="k", model="m"))
        self.assertEqual(result.raw["event_type"], "call")
        self.assertEqual(mock_urlopen.call_count, 2)


class TestResolver(unittest.TestCase):
    def test_relative_date_and_time(self):
        payload = resolve({
            "operation": "create", "event_type": "class", "title": "lab",
            "date_text": "maine", "time_text": "10:00", "duration_text": None,
            "end_time_text": None, "location_text": None, "recurrence_text": None,
            "reminder_texts": [],
        }, reference=date(2026, 9, 18))
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["start"], "2026-09-19T10:00:00+03:00")
        self.assertEqual(payload["duration_minutes"], 90)
        self.assertFalse(payload["ambiguous"])

    def test_ambiguous_bare_hour(self):
        payload = resolve({
            "operation": "create", "event_type": "meeting", "title": None,
            "date_text": "today", "time_text": "3", "duration_text": None,
            "end_time_text": None, "location_text": None, "recurrence_text": None,
            "reminder_texts": [],
        }, reference=date(2026, 9, 18))
        self.assertTrue(payload["ambiguous"])

    def test_unresolved_when_clock_missing(self):
        payload = resolve({
            "operation": "create", "event_type": "call", "title": None,
            "date_text": "maine", "time_text": "la pranz", "duration_text": None,
            "end_time_text": None, "location_text": None, "recurrence_text": None,
            "reminder_texts": [],
        }, reference=date(2026, 9, 18))
        self.assertIn("time_text", payload["unresolved"])
        self.assertFalse(payload["complete"])

    def test_reminder_half_and_full_hour(self):
        payload = resolve({
            "operation": "create", "event_type": "appointment", "title": None,
            "date_text": "maine", "time_text": "13:30", "duration_text": None,
            "end_time_text": None, "location_text": None, "recurrence_text": None,
            "reminder_texts": ["за два часа", "за полчаса"],
        }, reference=date(2026, 9, 18))
        self.assertEqual(payload["reminders_minutes"], [30, 120])
        self.assertNotIn("reminder_texts", payload["unresolved"])


class TestFormatResolved(unittest.TestCase):
    def test_structured_not_raw(self):
        text = format_resolved({
            "operation": "create", "event_type": "appointment",
            "title": "dentist <soon>", "start": "2026-09-19T13:30:00+03:00",
            "all_day": False, "duration_minutes": 90, "location": "Main St 16/2",
            "recurrence": None, "reminders_minutes": [30, 120],
            "ambiguous": False, "complete": True, "unresolved": [],
        })
        self.assertNotIn("{", text)
        self.assertIn("dentist &lt;soon&gt;", text)
        self.assertIn("1 h 30 min", text)
        self.assertIn("2 h, 30 min before", text)
        self.assertIn("13:30", text)


if __name__ == "__main__":
    unittest.main()
