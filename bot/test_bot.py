import asyncio
import json
import os
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest

from . import semantic
from .asr import ASRServiceError, TranscriptionResponse, format_duration, transcribe_voice
from .config import load_config
from .drafts import Draft, apply_answer, next_field
from .extraction import (
    Extraction,
    ExtractionServiceError,
    _parse_content,
    _request_body,
    extract_event,
)
from .handlers.voice import (
    _answer_draft,
    _question_keyboard,
    _question_text,
    format_resolved,
    handle_text,
    handle_voice,
    reply_event,
    run_extraction,
)


REFERENCE = datetime(2026, 9, 18, 12, 0, tzinfo=semantic.ZONE)


def semantic_raw(**updates):
    value = {
        "operation": "create",
        "event_type": "meeting",
        "title": "sync",
        "date": semantic._none_date(),
        "time": semantic._none_time(),
        "all_day": False,
        "duration_minutes": None,
        "duration_source": None,
        "end_time": semantic._none_time(),
        "location": None,
        "recurrence": None,
        "reminders": [],
        "corrected": False,
    }
    value.update(updates)
    return value


def complete_raw():
    raw = semantic_raw()
    raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
    raw["time"].update(
        kind="clock", source="09:00", hour=9, minute=0, meridiem="24h"
    )
    return raw


class TestASR(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0:00 (0s)")
        self.assertEqual(format_duration(65), "1:05 (65s)")

    @patch("bot.asr.urlopen")
    def test_transcribe_voice_success(self, mock_urlopen):
        response = MagicMock()
        response.read.return_value = b'{"text": "Meeting at 3 PM", "language_code": "eng"}'
        mock_urlopen.return_value.__enter__.return_value = response
        with tempfile.NamedTemporaryFile(suffix=".ogg") as audio:
            audio.write(b"dummy audio data")
            audio.flush()
            result = asyncio.run(transcribe_voice(Path(audio.name), api_key="dummy"))
        self.assertIsInstance(result, TranscriptionResponse)
        self.assertEqual(result.text, "Meeting at 3 PM")

    @patch("bot.asr.urlopen")
    def test_transcribe_voice_rejects_empty_text(self, mock_urlopen):
        response = MagicMock()
        response.read.return_value = b'{"text": "", "language_code": "eng"}'
        mock_urlopen.return_value.__enter__.return_value = response
        with tempfile.NamedTemporaryFile(suffix=".ogg") as audio:
            audio.write(b"dummy audio data")
            audio.flush()
            with self.assertRaises(ASRServiceError):
                asyncio.run(transcribe_voice(Path(audio.name), api_key="dummy"))


class TestConfig(unittest.TestCase):
    @patch("bot.config.load_dotenv")
    @patch.dict(os.environ, {
        "BOT_TOKEN": "123:ABC",
        "OWNER_ID": "999",
        "ELEVENLABS_API": "eleven",
        "DEEPSEEK_API": "deepseek",
    }, clear=True)
    def test_load_config_success(self, _mock_dotenv):
        config = load_config()
        self.assertEqual(config.deepseek_api_key, "deepseek")
        self.assertEqual(config.extraction_model, "deepseek-flash")
        self.assertEqual(config.extraction_timeout, 60)

    @patch("bot.config.load_dotenv")
    @patch.dict(os.environ, {
        "BOT_TOKEN": "123:ABC", "OWNER_ID": "999", "ELEVENLABS_API": "eleven",
    }, clear=True)
    def test_load_config_requires_deepseek(self, _mock_dotenv):
        with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API"):
            load_config()

    @patch("bot.config.load_dotenv")
    @patch.dict(os.environ, {
        "BOT_TOKEN": "123:ABC", "OWNER_ID": "999", "ELEVENLABS_API": "eleven",
        "DEEPSEEK_API": "deepseek", "EXTRACTION_TIMEOUT": "abc",
    }, clear=True)
    def test_load_config_bad_timeout(self, _mock_dotenv):
        with self.assertRaisesRegex(RuntimeError, "EXTRACTION_TIMEOUT"):
            load_config()


class TestExtraction(unittest.TestCase):
    def test_request_body_uses_deepseek_contract(self):
        messages = [{"role": "system", "content": "contract"}]
        body = _request_body("deepseek-flash", messages)
        self.assertEqual(body["messages"], messages)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["max_tokens"], 2048)

    def test_parse_content(self):
        self.assertEqual(_parse_content('```json\n{"operation": "create"}\n```'), {"operation": "create"})
        with self.assertRaises(ExtractionServiceError):
            _parse_content("not json")

    @patch("bot.extraction.urllib.request.urlopen")
    def test_extract_event_success(self, mock_urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"operation": "create"}'}}]
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = response
        result = asyncio.run(extract_event([], api_key="key", model="model"))
        self.assertEqual(result.raw, {"operation": "create"})

    @patch("bot.extraction.urllib.request.urlopen")
    def test_extract_event_retries_bad_model_json(self, mock_urlopen):
        def response(content):
            body = MagicMock()
            body.read.return_value = json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode()
            context = MagicMock()
            context.__enter__.return_value = body
            return context

        mock_urlopen.side_effect = [response("{"), response('{"operation": "create"}')]
        result = asyncio.run(extract_event([], api_key="key"))
        self.assertEqual(result.raw["operation"], "create")
        self.assertEqual(mock_urlopen.call_count, 2)


class TestRunExtraction(unittest.TestCase):
    @patch("bot.handlers.voice.extract_event", new_callable=AsyncMock)
    def test_uses_exact_reference(self, mock_extract):
        mock_extract.return_value = Extraction(complete_raw(), 1.2)
        raw, resolved, seconds, error = asyncio.run(run_extraction(
            "sync tomorrow 09:00",
            REFERENCE,
            deepseek_api_key="key",
            extraction_model="deepseek-flash",
            extraction_timeout=10,
        ))
        self.assertIsNone(error)
        self.assertEqual(seconds, 1.2)
        self.assertEqual(resolved["start"], "2026-09-19T09:00:00+03:00")
        messages = mock_extract.await_args.args[0]
        self.assertIn(REFERENCE.isoformat(), messages[1]["content"])
        self.assertEqual(raw["operation"], "create")

    @patch("bot.handlers.voice.extract_event", new_callable=AsyncMock)
    def test_sanitized_failure(self, mock_extract):
        mock_extract.side_effect = ExtractionServiceError("model returned invalid JSON")
        result = asyncio.run(run_extraction(
            "sync", REFERENCE, deepseek_api_key="secret",
            extraction_model="deepseek-flash", extraction_timeout=10,
        ))
        self.assertIsNone(result[0])
        self.assertIn("invalid JSON", result[3])
        self.assertNotIn("secret", result[3])


class TestDrafts(unittest.TestCase):
    def draft(self, raw, field, record_path):
        return Draft(deepcopy(raw), ["sync"], REFERENCE, field, record_path)

    def test_strict_date_and_time_patch_nested_json_without_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            draft = self.draft(semantic_raw(), "date", Path(directory) / "record.json")
            result = apply_answer(draft, "2026-09-20")
            self.assertEqual(draft.raw["date"]["kind"], "absolute")
            self.assertEqual(draft.awaiting, "time")
            result = apply_answer(draft, "18:30")
            self.assertEqual(result["start"], "2026-09-20T18:30:00+03:00")
            self.assertIsNone(next_field(result))

    def test_invalid_date_and_time_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            for field, answer in (("date", "tomorrow"), ("time", "nine")):
                with self.subTest(field=field):
                    draft = self.draft(semantic_raw(), field, path)
                    with self.assertRaises(ValueError):
                        apply_answer(draft, answer)
                    self.assertEqual(draft.evidence, ["sync"])

    def test_all_day_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = semantic_raw()
            raw["date"].update(
                kind="absolute", source="2026-09-20", year=2026, month=9, day=20
            )
            draft = self.draft(raw, "time", Path(directory) / "record.json")
            result = apply_answer(draft, "all day")
            self.assertTrue(result["all_day"])
            self.assertEqual(result["start"], "2026-09-20T00:00:00+03:00")


class TestPresentation(unittest.TestCase):
    def test_card_displays_readiness_and_risks(self):
        resolved = semantic.resolve(complete_raw(), "sync tomorrow 09:00", REFERENCE)
        text = format_resolved(resolved)
        self.assertIn("Ready for automatic creation", text)
        risky = {**resolved, "auto_write": False, "risks": ["weekday"]}
        self.assertIn("Needs confirmation", format_resolved(risky))
        self.assertIn("weekday", format_resolved(risky))

    def test_questions_publish_strict_formats(self):
        self.assertIn("YYYY-MM-DD", _question_text("date", {"title": "Sync"}))
        self.assertIn("HH:MM", _question_text("time", {"title": "Sync"}))
        date_data = [
            button.callback_data
            for row in _question_keyboard("date", REFERENCE.date()).inline_keyboard
            for button in row
        ]
        self.assertIn("ans:date:2026-09-18", date_data)
        time_data = [
            button.callback_data
            for row in _question_keyboard("time", REFERENCE.date()).inline_keyboard
            for button in row
        ]
        self.assertIn("ans:time:all day", time_data)


class TestReplyFlow(unittest.TestCase):
    def message(self, text="sync"):
        sent = MagicMock()
        sent.chat.id = -100
        sent.message_id = 555
        message = MagicMock()
        message.text = text
        message.message_id = 42
        message.from_user.id = 1
        message.answer = AsyncMock(return_value=sent)
        return message

    def test_incomplete_request_starts_draft_with_original_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            path.write_text("{}", encoding="utf-8")
            raw = semantic_raw()
            resolved = semantic.resolve(raw, "sync", REFERENCE)
            drafts = {}
            asyncio.run(reply_event(
                self.message(), MagicMock(), drafts, 1, raw, resolved, None,
                reference=REFERENCE, record_path=path, original_text="sync",
            ))
            self.assertEqual(drafts[1].reference, REFERENCE)
            self.assertEqual(drafts[1].awaiting, "date")
            self.assertEqual((drafts[1].chat_id, drafts[1].message_id), (-100, 555))

    def test_answer_updates_original_record_and_preserves_raw_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            original = semantic_raw()
            path.write_text(json.dumps({"extraction": original}), encoding="utf-8")
            draft = Draft(deepcopy(original), ["sync"], REFERENCE, "date", path)
            drafts = {1: draft}
            bot = MagicMock()
            bot.edit_message_text = AsyncMock()
            asyncio.run(_answer_draft(self.message("2026-09-20"), bot, drafts, 1, "2026-09-20"))
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["extraction"]["date"]["kind"], "none")
            self.assertEqual(record["effective_extraction"]["date"]["kind"], "absolute")
            self.assertEqual(record["clarification_answers"], ["2026-09-20"])

    def test_final_card_keeps_placeholder_buttons(self):
        raw = complete_raw()
        resolved = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        message = self.message()
        asyncio.run(reply_event(message, MagicMock(), {}, 1, raw, resolved, None))
        markup = message.answer.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for button in markup.inline_keyboard[0]]
        self.assertEqual(callbacks, ["confirm:42", "edit:42", "cancel:42"])

    def test_voice_reply_is_rejected_before_transcription(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            draft = Draft(semantic_raw(), ["sync"], REFERENCE, "date", path)
            message = self.message()
            message.voice = MagicMock()
            bot = MagicMock()
            asyncio.run(handle_voice(
                message, bot, {1: draft}, "eleven", "scribe_v2", "deepseek",
                "deepseek-flash", 60, Path(directory), 168, False,
            ))
            message.answer.assert_awaited_once()
            bot.download.assert_not_called()

    @patch("bot.handlers.voice.run_extraction", new_callable=AsyncMock)
    @patch("bot.handlers.voice.datetime")
    def test_text_record_contains_provenance(self, mock_datetime, mock_run):
        mock_datetime.now.return_value = REFERENCE
        raw = complete_raw()
        resolved = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        mock_run.return_value = (raw, resolved, 1.23, None)
        with tempfile.TemporaryDirectory() as directory:
            message = self.message("sync tomorrow 09:00")
            asyncio.run(handle_text(
                message, MagicMock(), {}, "deepseek", "deepseek-flash", 60,
                Path(directory), 168, False,
            ))
            record = json.loads(
                (Path(directory) / "1" / "42" / "record.json").read_text(encoding="utf-8")
            )
        self.assertEqual(record["extraction_model"], "deepseek-flash")
        self.assertEqual(record["contract_hash"], semantic.contract_hash())
        self.assertEqual(record["extraction_wait_time_seconds"], 1.23)
        self.assertEqual(record["extraction"], raw)


if __name__ == "__main__":
    unittest.main()
