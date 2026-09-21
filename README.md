# Voice Calendar Bot

Private aiogram bot that turns a voice message or plain text into a calendar event, with owner-managed access, admin ranks, file logging, and Docker deployment.

Allowed users send a voice message or plain text. Voice is transcribed with ElevenLabs Scribe v2; plain text skips ASR. DeepSeek converts the text into semantic JSON once, and deterministic code resolves date arithmetic, time, duration, defaults, reminders, recurrence, grounding, and timezone (`Europe/Chisinau`). The bot then shows an event card. If a required field is missing, it asks one focused deterministic question at a time. Clarifications do not call DeepSeek again: dates accept `YYYY-MM-DD` or quick replies, times accept `HH:MM` or quick replies, and voice clarification replies are rejected. The card and each question live in a single message that is edited in place as the draft evolves.

`auto_write` is an eligibility result, not an action in this release. It means
the request is a complete, grounded create with no remaining confirmation risk.
Grounded named weekdays, explicit durations, and explicit end times are
eligible. A named weekday means its next occurrence; on that weekday, it means
seven days later. Locations, recurrence, corrections, multiple reminders, past
starts, unknown operations, ungrounded fields, and DST ambiguity require review.

The final card carries `Confirm`, `Edit`, and `Cancel`. These are placeholders for now: the bot creates drafts only, and no Google Calendar writes happen yet.

Voice and text records are kept under `data/` with private permissions.

## Commands

- `/start` - verify bot is running.
- `/help` - show usage information.
- `/admin_help` - show administration commands.
- `/list_users` - open paginated user list with removal controls.
- `/adduser <user_id>` - add user.
- `/add_admin <user_id>` - promote user; owner only.
- `/rm_admin <user_id>` - demote admin; owner only.

## Roadmap

### Done

- [x] Set up the repository, Docker deployment, private access, and logging.
- [x] Receive voice messages, transcribe them with ElevenLabs Scribe v2, and retain records for 7 days.
- [x] Extend input to plain text messages, skipping ASR.
- [x] Build a fixed Romanian, Russian, and English ASR corpus and benchmark local models.
- [x] Select an ASR model and return transcripts, including code-switched Romanian, Russian, and English.
- [x] Benchmark extraction models and return a validated event schema.
- [x] Replace Mistral extraction and language-specific parsing with one DeepSeek semantic extraction call.
- [x] Extract operation, event type, title, date, time, duration, end time, location, reminders, and recurrence.
- [x] Resolve dates, times, durations, defaults, reminders, recurrence, grounding, and timezone rules with deterministic code.
- [x] Reject malformed or ungrounded model fields without guessing.
- [x] Benchmark DeepSeek semantic behavior across three runs and re-resolve archived frozen outputs under each new resolver contract.
- [x] Ask deterministic follow-up questions without another DeepSeek call: title text, `YYYY-MM-DD`, `HH:MM`, or quick replies.
- [x] Reject voice replies while a clarification question is active.
- [x] Update a single message in place as the draft evolves, for both typed and button answers.
- [x] Offer quick replies for date and time, including an `All day` option.
- [x] Store extraction model, contract hash, latency, original DeepSeek JSON, deterministic follow-up changes, and resolved result.
- [x] Deploy production DeepSeek semantic extraction.

### Next

- [ ] Choose Calendar identity and scope: one shared Calendar with a service account, or one Calendar connection per user through OAuth.
- [ ] Add Google Calendar authentication, client, and normalized event payload behind `CALENDAR_WRITE_ENABLED=false`.
- [ ] Persist a calendar-write record before each Google request: idempotency key, payload fingerprint, status, returned event ID, and error.
- [ ] With `CALENDAR_WRITE_ENABLED=false`, generate and store proposed Calendar payloads without calling Google.
- [ ] Review shadow records, then enable manual `Confirm` writes for review-required events.
- [ ] Show created-event details and Calendar link for both confirmed and automatic creates.
- [ ] Canary immediate creation for `auto_write=true` events; retain `CALENDAR_WRITE_ENABLED` as rollback.
- [ ] Wire `Cancel` to close final review cards without mutation.
- [ ] Read Calendar events and retain Google event references for later list and edit actions.
- [ ] Wire `Edit` to revise one event field at a time before writing.
- [ ] Search Google Maps with Moldova bias when an event includes a location.
- [ ] Show formatted addresses and preview links, then ask the user to choose when several places match.
- [ ] List previous events and select one to edit.
- [ ] Support general natural-language edits to existing events.

## Start

1. Create configuration, then fill in `BOT_TOKEN`, `OWNER_ID`, `ELEVENLABS_API`, and `DEEPSEEK_API`:

   ```bash
   cp .env.example .env
   ```

2. Create bind-mounted directories:

   ```bash
   mkdir -p data logs
   ```

3. Start bot:

   ```bash
   docker compose up --build -d
   ```

Set `DEBUG=true` in `.env` to also print the raw extraction JSON and the ASR/LLM wait, language, and cost summary. Left off, chats show only the transcript and the resolved card.

Access data lives in `data/access.json`. Console logs use colored levels. Plain file logs use Chisinau timestamps, one dated `bot_DD_MM_YY.log` file per day, and retain seven files by default in `logs/`.

Voice audio and metadata live under `data/voice/<user_id>/<message_id>/` (`audio.ogg` and `record.json`) with private permissions for a rolling 168 hours. Plain text records live under `data/text/`. Cleanup runs hourly and once at startup. Only the local mounted volume is managed by this policy; Telegram and ElevenLabs retention are controlled by those services. Legacy flat timestamp-named recordings were moved once to `data/voice-corpus/` and are not retention-managed. Maximum voice size is 2 MiB.

Set `HOST_UID` and `HOST_GID` in `.env` when bind-mounted directories belong to a user other than `1000:1000`.
