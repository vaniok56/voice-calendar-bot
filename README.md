# Voice Calendar Bot

Private aiogram bot that turns a voice message or plain text into a calendar event, with owner-managed access, admin ranks, file logging, and Docker deployment.

Allowed users send a voice message or plain text. Voice is transcribed with ElevenLabs Scribe v2; plain text skips ASR. A Mistral model copies the fields it hears into a fixed schema, and deterministic code resolves the date, time, duration, defaults, reminders, and timezone (`Europe/Chisinau`). The bot then shows an event card. If a required field is missing, it asks one focused question at a time, keeps the rest of the draft, and offers quick replies (for example `Today` / `Tomorrow`, or `09:00` / `All day`). The card and each question live in a single message that is edited in place as the draft evolves.

The final card carries `Confirm`, `Edit`, and `Cancel`. These are placeholders for now: the bot only creates drafts, and no Google Calendar writes happen yet.

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
- [x] Extract operation, event type, title, date, time, duration, end time, location, reminders, and recurrence.
- [x] Resolve dates, times, durations, defaults, reminders, and timezone rules with deterministic code.
- [x] Report missing, ambiguous, and unsupported fields without guessing.
- [x] Ask one focused question at a time and keep the rest of the draft while the user answers.
- [x] Update a single message in place as the draft evolves, for both typed and button answers.
- [x] Offer quick replies for date and time, including an `All day` option.

### Next

- [ ] Wire `Confirm`, `Edit`, and `Cancel` to actions.
- [ ] Prevent repeated button presses from creating duplicate events.
- [ ] Connect Google Calendar and support `CALENDAR_WRITE_ENABLED` to disable writes during testing.
- [ ] Create the confirmed event and return its details.
- [ ] Search Google Maps with Moldova bias when an event includes a location.
- [ ] Show formatted addresses and preview links, then ask the user to choose when several places match.
- [ ] Create events automatically only when measured confidence is high enough.
- [ ] Edit event fields one at a time.
- [ ] List previous events and select one to edit.
- [ ] Support general natural-language edits to existing events.

## Start

1. Create configuration, then fill in `BOT_TOKEN` and `OWNER_ID`:

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
