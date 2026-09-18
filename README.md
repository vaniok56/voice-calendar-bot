# Telegram Bot Template

Minimal private aiogram bot template with owner-managed access, admin ranks, file logging, and Docker deployment.

Allowed users can send voice messages directly. The bot downloads the audio, transcribes it with ElevenLabs Scribe v2, and replies with the transcript plus language and length.

## Commands

- `/start` - verify bot is running.
- `/help` - show voice storage information.
- `/admin_help` - show administration commands.
- `/list_users` - open paginated user list with removal controls.
- `/adduser <user_id>` - add user.
- `/add_admin <user_id>` - promote user; owner only.
- `/rm_admin <user_id>` - demote admin; owner only.

## Roadmap

### Done

- [x] Set up the repository, Docker deployment, private access, and logging.
- [x] Receive, queue, and store Telegram voice messages.
- [x] Build a fixed Romanian, Russian, and English ASR corpus and benchmark local models.

### Next

- [ ] Select an ASR model and return transcripts for Telegram voice messages.
- [ ] Support mixed-language recordings when the selected ASR model handles them reliably.
- [ ] Benchmark local models for structured event extraction.
- [ ] Extract operation, event type, title, date, time, duration, end time, location, reminders, and recurrence into a validated schema.
- [ ] Resolve dates, times, durations, defaults, reminders, and timezone rules with deterministic code.
- [ ] Detect missing, ambiguous, or unsupported fields without guessing.
- [ ] Ask one focused question and keep the rest of the draft while the user answers.
- [ ] Show an event summary with `Confirm`, `Retry`, and `Cancel` buttons.
- [ ] Prevent repeated button presses from creating duplicate events.
- [ ] Connect Google Calendar and support a setting that disables writes during testing.
- [ ] Create the confirmed event and return its details.
- [ ] Search Google Maps with Moldova bias when an event includes a location.
- [ ] Show formatted addresses and preview links, then ask the user to choose when several places match.
- [ ] Create events automatically only when measured confidence is high enough.
- [ ] Add a `Done` message with event details and an `Edit` button.
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

Access data lives in `data/access.json`. Console logs use colored levels. Plain file logs use Chisinau timestamps, one dated `bot_DD_MM_YY.log` file per day, and retain seven files by default in `logs/`.

Voice audio and metadata live under `data/voice/<user_id>/<message_id>/` (`audio.ogg` and `record.json`) with private permissions for a rolling 168 hours. Cleanup runs hourly and once at startup. Only the local mounted volume is managed by this policy; Telegram and ElevenLabs retention are controlled by those services. Legacy flat timestamp-named recordings were moved once to `data/voice-corpus/` and are not retention-managed. Maximum voice size is 2 MiB.

Set `HOST_UID` and `HOST_GID` in `.env` when bind-mounted directories belong to a user other than `1000:1000`.
