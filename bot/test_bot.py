import asyncio
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest

from .asr import (
    ASRServiceError,
    TranscriptionResponse,
    format_duration,
    transcribe_voice,
)
from .config import Config, load_config
from .drafts import Draft, apply_answer, next_field
from .extraction import Extraction, ExtractionServiceError, _parse_content, coerce, extract_event
from .handlers.voice import (
    _question_keyboard,
    _question_text,
    format_resolved,
    reply_event,
    run_extraction,
)
from .resolver import parse_date, resolve


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

    @patch("bot.config.load_dotenv")
    @patch.dict(os.environ, {
        "BOT_TOKEN": "123:ABC",
        "OWNER_ID": "999",
        "ELEVENLABS_API": "test_eleven_key",
        "MISTRAL_API": "test_mistral_key",
        "EXTRACTION_TIMEOUT": "abc",
    }, clear=True)
    def test_load_config_bad_int(self, _mock_dotenv):
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

    def test_coerce_out_of_enum(self):
        coerced = coerce({"operation": "remind", "event_type": "party",
                          "reminder_texts": "oops"})
        self.assertEqual(coerced["operation"], "create")
        self.assertEqual(coerced["event_type"], "other")
        self.assertEqual(coerced["reminder_texts"], [])

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

    def test_trip_duration_is_not_zero(self):
        payload = resolve({
            "operation": "create", "event_type": "trip", "title": "excursie",
            "date_text": "sambata", "time_text": "11:00", "duration_text": None,
            "end_time_text": None, "location_text": None, "recurrence_text": None,
            "reminder_texts": [],
        }, reference=date(2026, 9, 18))
        self.assertIsNone(payload["duration_minutes"])

    def test_unsupported_recurrence_reported(self):
        payload = resolve({
            "operation": "create", "event_type": "meeting", "title": "sync",
            "date_text": "maine", "time_text": "10:00", "duration_text": None,
            "end_time_text": None, "location_text": None, "recurrence_text": "каждый день",
            "reminder_texts": [],
        }, reference=date(2026, 9, 18))
        self.assertIsNone(payload["recurrence"])
        self.assertIn("recurrence_text", payload["unresolved"])

    def test_all_day_keeps_date_and_needs_one(self):
        payload = resolve({
            "operation": "create", "event_type": "birthday", "title": "Ana",
            "date_text": "maine",
        }, reference=date(2026, 9, 19))
        self.assertTrue(payload["all_day"])
        self.assertEqual(payload["date"], "2026-09-20")
        self.assertEqual(payload["start"], "2026-09-20T00:00:00+03:00")
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["missing"], [])

        dateless = resolve({
            "operation": "create", "event_type": "birthday", "title": "Ana",
        }, reference=date(2026, 9, 19))
        self.assertFalse(dateless["complete"])
        self.assertEqual(dateless["missing"], ["date_text"])

    def test_all_day_marker_for_any_type(self):
        for phrase in ("All day", "all-day", "toată ziua", "весь день"):
            payload = resolve({
                "operation": "create", "event_type": "appointment", "title": "Dentist",
                "date_text": "maine", "time_text": phrase,
            }, reference=date(2026, 9, 19))
            self.assertTrue(payload["all_day"], phrase)
            self.assertEqual(payload["date"], "2026-09-20")
            self.assertTrue(payload["complete"], phrase)
            self.assertEqual(payload["missing"], [], phrase)

    def test_iso_date_from_button_callback(self):
        self.assertEqual(parse_date("2026-09-20", date(2026, 9, 19)), date(2026, 9, 20))

    def test_missing_fields_for_create(self):
        payload = resolve({
            "operation": "create", "event_type": "meeting",
        }, reference=date(2026, 9, 18))
        self.assertEqual(payload["missing"], ["title", "date_text", "time_text"])
        list_payload = resolve({
            "operation": "list", "event_type": "other",
        }, reference=date(2026, 9, 18))
        self.assertEqual(list_payload["missing"], [])


class TestRunExtraction(unittest.TestCase):
    @patch("bot.handlers.voice.extract_event", new_callable=AsyncMock)
    def test_success(self, mock_extract):
        mock_extract.return_value = Extraction(
            raw={"operation": "create", "event_type": "class", "date_text": "today",
                 "time_text": "10:00"},
            wait_time_seconds=1.2,
        )
        import asyncio
        raw, resolved, seconds, error = asyncio.run(run_extraction(
            "lab", mistral_api_key="k", extraction_model="m", extraction_timeout=10,
        ))
        self.assertIsNone(error)
        self.assertEqual(seconds, 1.2)
        self.assertEqual(raw["operation"], "create")
        self.assertTrue(resolved["complete"])

    @patch("bot.handlers.voice.extract_event", new_callable=AsyncMock)
    def test_failure(self, mock_extract):
        mock_extract.side_effect = ExtractionServiceError("model returned invalid JSON")
        import asyncio
        raw, resolved, seconds, error = asyncio.run(run_extraction(
            "lab", mistral_api_key="k", extraction_model="m", extraction_timeout=10,
        ))
        self.assertIsNone(raw)
        self.assertIsNone(resolved)
        self.assertIn("invalid JSON", error)


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


class TestDraftFlow(unittest.TestCase):
    REFERENCE = date(2026, 9, 19)

    def test_next_field(self):
        self.assertIsNone(next_field({"missing": []}))
        self.assertEqual(next_field({"missing": ["title", "time_text"]}), "title")

    def test_begin_keeps_independent_copy(self):
        drafts = {}
        raw = {"operation": "create"}
        drafts[1] = Draft(raw=dict(raw), awaiting="title")
        raw["title"] = "mutated"
        self.assertIsNone(drafts[1].raw.get("title"))

    def test_answers_complete_draft_one_field_at_a_time(self):
        draft = Draft(raw={"operation": "create", "event_type": "meeting"}, awaiting="title")
        resolve_fn = lambda raw: resolve(raw, reference=self.REFERENCE)

        apply_answer(draft, "sync", resolve_fn)
        self.assertEqual(draft.awaiting, "date_text")

        apply_answer(draft, "maine", resolve_fn)
        self.assertEqual(draft.awaiting, "time_text")

        resolved = apply_answer(draft, "10:00", resolve_fn)
        self.assertEqual(next_field(resolved), None)
        self.assertEqual(resolved["start"], "2026-09-20T10:00:00+03:00")
        self.assertTrue(resolved["complete"])

    def test_unparseable_answer_reasks_same_field(self):
        draft = Draft(
            raw={"operation": "create", "event_type": "meeting", "title": "sync"},
            awaiting="date_text",
        )
        resolved = apply_answer(
            draft, "la pranz", lambda raw: resolve(raw, reference=self.REFERENCE)
        )
        self.assertEqual(draft.awaiting, "date_text")
        self.assertFalse(resolved["complete"])


class TestQuestions(unittest.TestCase):
    REFERENCE = date(2026, 9, 19)

    def test_question_text_uses_title(self):
        resolved = {"title": "Dentist"}
        self.assertEqual(_question_text("title", resolved), "❓ What should I call this event?")
        self.assertIn("<b>Dentist</b>", _question_text("time_text", resolved))

    def test_date_keyboard_offers_today_and_tomorrow(self):
        markup = _question_keyboard("date_text", self.REFERENCE)
        data = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("ans:date_text:2026-09-19", data)
        self.assertIn("ans:date_text:2026-09-20", data)
        self.assertIn("q_cancel", data)

    def test_time_keyboard_has_all_day(self):
        markup = _question_keyboard("time_text", self.REFERENCE)
        data = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("ans:time_text:all day", data)
        self.assertIn("ans:time_text:09:00", data)


class TestReplyEvent(unittest.TestCase):
    REFERENCE = date(2026, 9, 19)

    def _message(self):
        sent = MagicMock()
        sent.chat.id = -100
        sent.message_id = 555
        message = MagicMock()
        message.message_id = 42
        message.answer = AsyncMock(return_value=sent)
        return message

    def test_first_turn_stores_prompt_handle(self):
        message = self._message()
        drafts = {}
        resolved = resolve(
            {"operation": "create", "event_type": "meeting"}, reference=self.REFERENCE
        )
        asyncio.run(reply_event(message, MagicMock(), drafts, 1, {}, resolved, None))
        draft = drafts.get(1)
        self.assertEqual(draft.awaiting, "title")
        self.assertEqual((draft.chat_id, draft.message_id), (-100, 555))

    def test_typed_answer_edits_stored_message(self):
        resolved = resolve(
            {"operation": "create", "event_type": "meeting", "title": "sync"},
            reference=self.REFERENCE,
        )
        cases = {
            "edit": None,
            "not_modified": TelegramBadRequest(
                method=MagicMock(), message="message is not modified"
            ),
        }
        for name, side_effect in cases.items():
            with self.subTest(name):
                message = self._message()
                bot = MagicMock()
                bot.edit_message_text = AsyncMock(side_effect=side_effect)
                drafts = {1: Draft({}, "date_text", chat_id=-100, message_id=555)}
                asyncio.run(reply_event(message, bot, drafts, 1, {}, resolved, None))
                self.assertEqual(bot.edit_message_text.await_args.kwargs["chat_id"], -100)
                self.assertEqual(bot.edit_message_text.await_args.kwargs["message_id"], 555)
                message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
