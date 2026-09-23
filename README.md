# Voice Calendar Bot

Private aiogram bot that turns a voice message or plain text into a per-user Google Calendar event, with owner-managed access, admin ranks, file logging, and Docker deployment.

Allowed users send a voice message or plain text. Voice is transcribed with ElevenLabs Scribe v2; plain text skips ASR. DeepSeek converts the text into semantic JSON once, and deterministic code resolves date arithmetic, time, duration, defaults, reminders, recurrence, grounding, and each user's timezone. The bot then shows an event card. If a required field is missing, it asks one focused deterministic question at a time. Clarifications do not call DeepSeek again: dates accept `YYYY-MM-DD` or quick replies, times accept `HH:MM` or quick replies, and voice clarification replies are rejected. The card and each question live in a single message that is edited in place as the draft evolves.

Timezone and duration defaults now come from each user's private profile, initially
seeded from `CALENDAR_TIMEZONE`. Custom types can match semantically (e.g. workout
→ Gym); their durations apply unless the request specifies an end time or duration.

`auto_write` is an eligibility result. It means
the request is a complete, grounded create with no remaining confirmation risk.
Grounded named weekdays, explicit durations, and explicit end times are
eligible. A named weekday means its next occurrence; on that weekday, it means
seven days later. Locations, recurrence, corrections, multiple reminders, past
starts, unknown operations, ungrounded fields, conflicting end time and duration,
and DST ambiguity require review.

Each user connects their own Google Calendar through `/settings` and its
inline authorization button. Each `/settings` moves the card to the bottom of chat
and removes the previous card when Telegram permits. Connect shows the OAuth link
on the card; Back invalidates the link and restores settings. A successful Google
callback updates the card and sends a private confirmation. `/settings` shows the
connected email, Calendar status, automatic-write preference, user timezone, and
two-column Built-in durations and Custom types submenus. Tap a type to change its
duration; add a custom type by replying with its name and then duration in minutes.
Timezone edits take exact IANA names (e.g. `Europe/Chisinau`). Settings text flows
expire after ten minutes. Enabling automatic writes requires explicit confirmation.
Existing
connections must reconnect to grant email identity access; old tokens cannot
write events. Disconnect asks for confirmation, then tries to revoke the Google
refresh token and removes the local credential. Admins see the global Google
writes switch in `/admin_help`; it is not shown in `/settings`. With
`CALENDAR_WRITE_ENABLED=false`, the bot stores normalized payloads in shadow
mode and never calls Google. With writes enabled, complete events offer manual
`Confirm`, `Edit`, and `Cancel` by default. Only opted-in users' safe, complete
events are created immediately; risky or incomplete events never bypass review.
`Edit` supports title, date, and time before creation. User profile lives in
`data/google-calendar/profiles/<user_id>.json` with private permissions.

Voice and text records are kept under `data/` with private permissions.

## Commands

- `/start` - verify bot is running.
- `/help` - show usage information.
- `/settings` - manage Calendar account, automatic writes, timezone, built-in durations, and custom types.
- `/admin_help` - show administration commands and global Google writes status (admins only).
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
- [x] Add per-user Google Calendar OAuth with PKCE, private token storage, and connect/disconnect commands.
- [x] Convert resolved events to timed, all-day, reminder, and recurring Google Calendar payloads.
- [x] Persist idempotent Calendar writes with stable event IDs and connection-generation checks.
- [x] Add shadow mode, automatic safe writes, and Confirm/Edit/Cancel review for risky events.
- [x] Add CI checks with read-only permissions and monthly Dependabot updates.
- [x] Show connected account and timezone, require verified email for OAuth, revoke on disconnect, and recover expired `creating` writes.

### Next

- [ ] Complete production shadow-mode payload review.
- [ ] Review Branch 2 `/settings` flow and per-user profile behavior in practice.
- [ ] Enable global Calendar writes with per-user auto-write disabled, allowing manual `Confirm` writes only.
- [ ] Canary per-user automatic creation; retain `CALENDAR_WRITE_ENABLED` as global rollback.
- [ ] Reassess incremental editor fields from production usage.
- [ ] Add deployment health checks, backup/rollback steps, and a manual deployment workflow.
- [ ] Search Google Maps with Moldova bias when an event includes a location.
- [ ] Show formatted addresses and preview links, then ask the user to choose when several places match.

## Dependencies

`requirements.in` lists direct runtime dependencies. `requirements.txt` is the
hash-checked production lock file. To intentionally update it, use Python 3.12:

```bash
python -m pip install pip-tools==7.6.1
pip-compile --generate-hashes --no-header --output-file requirements.txt requirements.in
```

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

Voice audio and metadata live under `data/voice/<user_id>/<message_id>/` (`audio.ogg` and `record.json`) with private permissions for a rolling 168 hours. Plain text records live under `data/text/`. Cleanup runs hourly and once at startup. Only the local mounted volume is managed by this policy; Telegram and ElevenLabs retention are controlled by those services. Legacy flat timestamp-named recordings live in `data/voice-corpus/` and are not retention-managed; local ASR benchmark runner instead reads its paired corpus from `data/voice/`. Maximum voice size is 2 MiB.

Set `HOST_UID` and `HOST_GID` in `.env` when bind-mounted directories belong to a user other than `1000:1000`.

## Google Calendar Setup

1. Create dedicated Google Cloud project, enable Google Calendar API, and configure OAuth consent screen as External. Add initial Gmail account as test user.
2. Create Web application OAuth client. Add exact redirect URI: `https://calendar.<your-domain>/google/callback`.
3. Add `https://www.googleapis.com/auth/calendar.events.owned`, `openid`, and `https://www.googleapis.com/auth/userinfo.email` to OAuth data-access configuration. Reconnect existing test users after deployment; `/settings` shows `Reconnect required` until then.
4. Create Cloudflare named tunnel with public hostname `calendar.<your-domain>` routed to `http://bot:8080`. Do not protect callback hostname with Cloudflare Access.
5. Generate a token-encryption key on Reactor with `python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'`. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, `CALENDAR_TOKEN_ENCRYPTION_KEY`, and `CALENDAR_TUNNEL_TOKEN` in Reactor `.env`. Keep secrets, authorization codes, and token files out of Git and logs. Existing plaintext token files are encrypted on their next read.
6. Deploy tunnel with `docker compose --profile calendar up --build -d`. No host port mapping is needed.
7. Leave `CALENDAR_WRITE_ENABLED=false`; connect Gmail through `/settings`, check `/settings` shows correct verified email, create events in Romanian, Russian, English, and mixed speech, then inspect `data/google-calendar/writes/`. Enable only after review. When writes are enabled, `creating` records can retry after ten minutes; retries reuse the stored Google event ID.

Testing OAuth clients can require users to reconnect every seven days. Publish the External consent screen before production use; Calendar scopes may require Google verification.
