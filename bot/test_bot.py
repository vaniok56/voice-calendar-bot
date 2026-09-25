import asyncio
import json
import os
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest

from . import semantic
from .asr import ASRServiceError, TranscriptionResponse, format_duration, transcribe_voice
from .calendar import load_write, save_token
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
    _calendar_link_markup,
    _calendar_markup,
    _calendar_text,
    _question_keyboard,
    _question_text,
    format_resolved,
    confirm_calendar_write,
    handle_text,
    handle_voice,
    reply_event,
    run_extraction,
)
from .handlers.admin import admin_help
from .profile import load_profile, save_profile


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

    @patch("bot.config.load_dotenv")
    @patch.dict(os.environ, {
        "BOT_TOKEN": "123:ABC", "OWNER_ID": "999", "ELEVENLABS_API": "eleven",
        "DEEPSEEK_API": "deepseek", "GOOGLE_OAUTH_CLIENT_ID": "client",
        "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
        "GOOGLE_OAUTH_REDIRECT_URI": "https://calendar.example.com/callback",
    }, clear=True)
    def test_load_config_requires_calendar_token_key(self, _mock_dotenv):
        with self.assertRaisesRegex(RuntimeError, "CALENDAR_TOKEN_ENCRYPTION_KEY"):
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
    def test_follow_up_keeps_profile_zone_and_duration_snapshot(self):
        raw = semantic_raw()
        raw["date"].update(kind="relative", source="tomorrow", offset_days=1)
        draft = Draft(raw, ["sync tomorrow"], REFERENCE, "time", Path("record.json"),
                      timezone="Asia/Tokyo", profile={"type_durations": {"meeting": 45},
                                                      "custom_types": [], "timezone": "Asia/Tokyo", "revision": 1})
        resolved = apply_answer(draft, "09:00")
        self.assertEqual(resolved["start"], "2026-09-19T09:00:00+09:00")
        self.assertEqual((resolved["duration_minutes"], resolved["duration_origin"]), (45, "profile_builtin"))

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
    def test_admin_help_alone_shows_global_writes(self):
        message = MagicMock()
        message.from_user.id = 1
        message.answer = AsyncMock()
        storage = SimpleNamespace(is_admin=lambda user_id: user_id == 1)
        asyncio.run(admin_help(message, storage, SimpleNamespace(calendar_write_enabled=True)))
        self.assertIn("Google writes: On ✅", message.answer.await_args.args[0])
        message.answer.reset_mock()
        asyncio.run(admin_help(message, storage, SimpleNamespace(calendar_write_enabled=False)))
        self.assertIn("Google writes: Off ❌", message.answer.await_args.args[0])
        message.answer.reset_mock()
        message.from_user.id = 2
        asyncio.run(admin_help(message, storage, SimpleNamespace(calendar_write_enabled=True)))
        message.answer.assert_not_awaited()

    def test_card_displays_readiness_and_risks(self):
        resolved = semantic.resolve(complete_raw(), "sync tomorrow 09:00", REFERENCE)
        text = format_resolved(resolved, REFERENCE)
        self.assertIn("When:</b> Tomorrow · 09:00", text)
        self.assertIn("Safe to create without review when automatic writes are enabled", text)
        risky = {**resolved, "auto_write": False, "risks": ["weekday"]}
        self.assertIn("Needs confirmation", format_resolved(risky, REFERENCE))
        self.assertIn("weekday", format_resolved(risky, REFERENCE))

    def test_calendar_card_is_human_readable(self):
        payload = {
            "summary": "придет Рома",
            "start": {"dateTime": "2026-09-25T18:00:00+03:00", "timeZone": "Europe/Chisinau"},
            "end": {"dateTime": "2026-09-25T19:00:00+03:00", "timeZone": "Europe/Chisinau"},
            "recurrence": ["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=FR"],
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
        }
        text = _calendar_text(
            payload,
            "✅ Created",
            confirmation=True,
            now=datetime.fromisoformat("2026-09-25T12:00:00+03:00"),
        )
        self.assertIn("Done — added to your calendar.", text)
        self.assertIn("<b>When:</b> Today · 18:00 – 19:00 (1h)", text)
        self.assertIn("↻ Repeats weekly on Friday", text)
        self.assertIn("🔔 30 min before", text)
        self.assertNotIn("RRULE", text)
        self.assertNotIn("Open in Google Calendar", text)

    def test_calendar_card_all_day_and_relative(self):
        payload = {
            "summary": "Birthday",
            "start": {"date": "2026-09-26"},
            "end": {"date": "2026-09-27"},
        }
        text = _calendar_text(payload, "✅ Created", now=datetime(2026, 9, 25, 12, 0))
        self.assertIn("<b>When:</b> Tomorrow · All day", text)
        self.assertNotIn("Done", text)

    def test_calendar_link_is_button_not_preview(self):
        markup = _calendar_link_markup("https://calendar.google.com/event?eid=abc")
        self.assertEqual(
            markup.inline_keyboard[0][0].url, "https://calendar.google.com/event?eid=abc"
        )
        self.assertIsNone(_calendar_link_markup(None))

    def test_multiple_recurrence_weekdays_shown_and_date_valid(self):
        raw = semantic_raw()
        raw["date"].update(kind="weekday", source="every Tuesday and Friday", weekday=[1, 4])
        raw["time"].update(kind="clock", source="18:00", hour=18, minute=0, meridiem="24h")
        raw["recurrence"] = {
            "source": "every Tuesday and Friday", "freq": "weekly", "interval": 1,
            "weekdays": [1, 4], "month_day": None, "month": None,
            "position": None, "count": None, "until": None,
        }
        resolved = semantic.resolve(
            raw, "every Tuesday and Friday driving school 18:00", REFERENCE
        )
        self.assertNotIn("invalid_date", resolved["errors"])
        self.assertEqual(resolved["recurrence"]["weekdays"], [1, 4])
        self.assertIn("Repeats:</b> weekly on Tue, Fri", format_resolved(resolved, REFERENCE))

    def test_resolved_when_shows_today_and_tomorrow(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=semantic.ZONE)
        base = {
            "event_type": "meeting",
            "recurrence": None,
            "reminders_minutes": [],
            "auto_write": False,
        }

        def card_on(day):
            start = datetime.combine(day, time(16, 0), tzinfo=semantic.ZONE)
            return format_resolved({**base, "start": start.isoformat()}, now)

        self.assertIn("<b>When:</b> Today · 16:00", card_on(now.date()))
        self.assertIn(
            "<b>When:</b> Tomorrow · 16:00", card_on(now.date() + timedelta(days=1))
        )
        later = now.date() + timedelta(days=5)
        self.assertIn(
            f"<b>When:</b> {later.strftime('%a, %d %b %Y')} · 16:00", card_on(later)
        )
        all_day = {**base, "all_day": True, "date": now.date().isoformat()}
        self.assertIn("<b>When:</b> Today · All day", format_resolved(all_day, now))

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

    def test_final_card_has_no_calendar_controls_without_oauth(self):
        raw = complete_raw()
        resolved = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        message = self.message()
        config = SimpleNamespace(
            data_dir=Path("."),
            google_oauth_client_id="",
            google_oauth_client_secret="",
            google_oauth_redirect_uri="",
        )
        asyncio.run(reply_event(
            message, MagicMock(), {}, 1, raw, resolved, None,
            record_path=Path("record.json"), config=config,
        ))
        self.assertIsNone(message.answer.await_args.kwargs["reply_markup"])

    def test_calendar_edit_selection_has_back_button(self):
        from .handlers.voice import _edit_fields_markup

        callbacks = [
            button.callback_data
            for row in _edit_fields_markup("abc123", False).inline_keyboard
            for button in row
        ]
        self.assertIn("edit_back:abc123", callbacks)

    def test_failed_calendar_write_has_retry_only(self):
        markup = _calendar_markup("abc123", False, retry=True)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertEqual(callbacks, ["confirm:abc123"])

    def calendar_config(self, data_dir, enabled):
        return SimpleNamespace(
            data_dir=Path(data_dir),
            calendar_timezone="Europe/Chisinau",
            calendar_write_enabled=enabled,
            google_oauth_client_id="client",
            google_oauth_client_secret="secret",
            google_oauth_redirect_uri="https://calendar.example.com/google/callback",
            calendar_token_encryption_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        )

    def calendar_record_path(self, directory):
        path = Path(directory) / "text" / "1" / "42" / "record.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
        return path

    def calendar_token(self, generation=0):
        return {
            "access_token": "access", "refresh_token": "refresh",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "connection_generation": generation, "schema_version": 2,
            "account_email": "user@example.com",
            "scope": ["openid", "https://www.googleapis.com/auth/userinfo.email",
                      "https://www.googleapis.com/auth/calendar.events.owned"],
        }

    def test_disabled_mode_shadows_without_google_request(self):
        raw = complete_raw()
        resolved = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        with tempfile.TemporaryDirectory() as directory:
            config = self.calendar_config(directory, False)
            save_token(config.data_dir, 1, self.calendar_token(), config.calendar_token_encryption_key)
            message = self.message()
            asyncio.run(reply_event(
                message, MagicMock(), {}, 1, raw, resolved, None,
                record_path=self.calendar_record_path(directory), config=config,
            ))
            write_path = next((Path(directory) / "google-calendar" / "writes").glob("*.json"))
            write = load_write(config.data_dir, write_path.stem)
        self.assertEqual(write["status"], "shadowed")
        self.assertIn("Shadowed", message.answer.await_args.args[0])

    def test_legacy_token_cannot_create_write(self):
        raw = complete_raw()
        resolved = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        with tempfile.TemporaryDirectory() as directory:
            config = self.calendar_config(directory, True)
            save_token(config.data_dir, 1, {
                "access_token": "access", "refresh_token": "refresh",
                "connection_generation": 0,
            }, config.calendar_token_encryption_key)
            message = self.message()
            asyncio.run(reply_event(
                message, MagicMock(), {}, 1, raw, resolved, None,
                record_path=self.calendar_record_path(directory), config=config,
            ))
            self.assertIn("Reconnect required", message.answer.await_args.args[0])
            self.assertFalse((config.data_dir / "google-calendar" / "writes").exists())

    @patch("bot.handlers.voice.insert_event", new_callable=AsyncMock)
    def test_auto_write_creates_once_when_connected(self, mock_insert):
        raw = complete_raw()
        resolved = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        mock_insert.return_value = {"id": "google-event", "htmlLink": "https://calendar.google/event"}
        with tempfile.TemporaryDirectory() as directory:
            config = self.calendar_config(directory, True)
            profile = load_profile(config.data_dir, 1, config.calendar_timezone)
            profile["auto_write_enabled"] = True
            save_profile(config.data_dir, 1, config.calendar_timezone, profile)
            save_token(config.data_dir, 1, self.calendar_token(), config.calendar_token_encryption_key)
            asyncio.run(reply_event(
                self.message(), MagicMock(), {}, 1, raw, resolved, None,
                record_path=self.calendar_record_path(directory), config=config,
            ))
        mock_insert.assert_awaited_once()

    @patch("bot.handlers.voice.insert_event", new_callable=AsyncMock)
    def test_auto_write_rejects_stale_token_generation(self, mock_insert):
        raw = complete_raw()
        resolved = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        with tempfile.TemporaryDirectory() as directory:
            config = self.calendar_config(directory, True)
            profile = load_profile(config.data_dir, 1, config.calendar_timezone)
            profile["auto_write_enabled"] = True
            save_profile(config.data_dir, 1, config.calendar_timezone, profile)
            save_token(config.data_dir, 1, self.calendar_token(1), config.calendar_token_encryption_key)
            message = self.message()
            asyncio.run(reply_event(
                message, MagicMock(), {}, 1, raw, resolved, None,
                record_path=self.calendar_record_path(directory), config=config,
            ))
        mock_insert.assert_not_awaited()
        self.assertIn("creation failed", message.answer.await_args.args[0])

    @patch("bot.handlers.voice.insert_event", new_callable=AsyncMock)
    def test_auto_write_requires_live_opt_in_and_resolver_safety(self, mock_insert):
        raw = complete_raw()
        safe = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        with tempfile.TemporaryDirectory() as directory:
            config = self.calendar_config(directory, True)
            save_token(config.data_dir, 1, self.calendar_token(), config.calendar_token_encryption_key)
            record = self.calendar_record_path(directory)
            message = self.message()
            asyncio.run(reply_event(message, MagicMock(), {}, 1, raw, safe, None,
                                    record_path=record, config=config))
            self.assertIn("Automatic writes off; confirm", message.answer.await_args.args[0])
            profile = load_profile(config.data_dir, 1, config.calendar_timezone)
            profile["auto_write_enabled"] = True
            save_profile(config.data_dir, 1, config.calendar_timezone, profile)
            risky = {**safe, "risks": ["location"], "auto_write": False}
            asyncio.run(reply_event(self.message(), MagicMock(), {}, 1, raw, risky, None,
                                    record_path=record, config=config))
            mock_insert.assert_not_awaited()
            profile = load_profile(config.data_dir, 1, config.calendar_timezone)
            profile["auto_write_enabled"] = False
            save_profile(config.data_dir, 1, config.calendar_timezone, profile)
            asyncio.run(reply_event(self.message(), MagicMock(), {}, 1, raw, safe, None,
                                    record_path=record, config=config,
                                    profile={**profile, "auto_write_enabled": True}))
            mock_insert.assert_not_awaited()

    @patch("bot.handlers.voice.insert_event", new_callable=AsyncMock)
    @patch("bot.handlers.voice.refresh_access_token", new_callable=AsyncMock)
    def test_disabling_auto_write_during_refresh_prevents_insert(self, refresh, insert):
        raw = complete_raw()
        resolved = semantic.resolve(raw, "sync tomorrow 09:00", REFERENCE)
        with tempfile.TemporaryDirectory() as directory:
            config = self.calendar_config(directory, True)
            profile = load_profile(config.data_dir, 1, config.calendar_timezone)
            profile["auto_write_enabled"] = True
            save_profile(config.data_dir, 1, config.calendar_timezone, profile)
            token = self.calendar_token()
            token["expires_at"] = "2000-01-01T00:00:00+00:00"
            save_token(config.data_dir, 1, token, config.calendar_token_encryption_key)

            async def disable(_config, _token):
                current = load_profile(config.data_dir, 1, config.calendar_timezone)
                current["auto_write_enabled"] = False
                save_profile(config.data_dir, 1, config.calendar_timezone, current)
                return {"access_token": "refreshed", "expires_at": "2099-01-01T00:00:00+00:00"}

            refresh.side_effect = disable
            message = self.message()
            asyncio.run(reply_event(message, MagicMock(), {}, 1, raw, resolved, None,
                                    record_path=self.calendar_record_path(directory), config=config))
            write_path = next((config.data_dir / "google-calendar" / "writes").glob("*.json"))
            self.assertEqual(load_write(config.data_dir, write_path.stem)["status"], "pending")
            self.assertIn("confirm to create", message.answer.await_args.args[0])
        insert.assert_not_awaited()

    @patch("bot.handlers.voice.insert_event", new_callable=AsyncMock)
    def test_confirm_retry_does_not_duplicate_event(self, mock_insert):
        raw = complete_raw()
        raw["location"] = "office"
        resolved = semantic.resolve(raw, "sync tomorrow 09:00 office", REFERENCE)
        mock_insert.return_value = {"id": "google-event"}
        with tempfile.TemporaryDirectory() as directory:
            config = self.calendar_config(directory, True)
            save_token(config.data_dir, 1, self.calendar_token(), config.calendar_token_encryption_key)
            asyncio.run(reply_event(
                self.message(), MagicMock(), {}, 1, raw, resolved, None,
                record_path=self.calendar_record_path(directory), config=config,
            ))
            write_path = next((Path(directory) / "google-calendar" / "writes").glob("*.json"))
            callback = MagicMock()
            callback.data = f"confirm:{write_path.stem}"
            callback.from_user.id = 1
            callback.answer = AsyncMock()
            callback.message.edit_text = AsyncMock()
            asyncio.run(confirm_calendar_write(callback, config))
            asyncio.run(confirm_calendar_write(callback, config))
        mock_insert.assert_awaited_once()

    @patch("bot.handlers.voice.insert_event", new_callable=AsyncMock)
    def test_disabled_retry_preserves_failed_record(self, mock_insert):
        with tempfile.TemporaryDirectory() as directory:
            config = self.calendar_config(directory, False)
            from .calendar import create_write, update_write

            write = create_write(config.data_dir, 1, "text/1/42/record.json", {
                "id": "a1234", "summary": "Sync", "start": {"date": "2026-09-19"},
            })
            update_write(config.data_dir, write["write_id"], status="failed", error="Previous failure")
            callback = MagicMock()
            callback.data = f"confirm:{write['write_id']}"
            callback.from_user.id = 1
            callback.answer = AsyncMock()
            callback.message.edit_text = AsyncMock()
            asyncio.run(confirm_calendar_write(callback, config))
            self.assertEqual(load_write(config.data_dir, write["write_id"])["status"], "failed")
            self.assertEqual(load_write(config.data_dir, write["write_id"])["error"], "Previous failure")
            mock_insert.assert_not_awaited()

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
