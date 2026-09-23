# Repository Guide

## Scope

Private Python 3.12 Telegram bot. Voice and text become personal Google Calendar events. Production entry point is `python -m bot.main`; Docker runs that command.

Core pipeline:

```text
Telegram update -> AllowlistMiddleware -> ASR for voice -> DeepSeek extraction
-> semantic.resolve -> draft/review -> shadow or Google Calendar write
```

Keep this split intact: model extracts source-grounded semantic fields; deterministic code resolves and decides safety. Never move date/time parsing, defaults, risk classification, or authorization policy into prompt-only behavior.

## Repository Map

| Path | Responsibility |
| --- | --- |
| `bot/main.py` | Process bootstrap, config/logging, polling, callback server, cleanup tasks. |
| `bot/config.py` | Environment parsing and fail-fast validation. |
| `bot/middleware.py` | Global private allowlist. |
| `bot/storage.py` | Atomic access-list persistence. |
| `bot/asr.py` | ElevenLabs Scribe HTTP adapter. |
| `bot/extraction.py` | DeepSeek JSON extraction HTTP adapter. |
| `bot/semantic.py` | Model schema/prompt and deterministic resolver. Highest-risk behavior. |
| `bot/drafts.py` | In-memory deterministic clarification state. |
| `bot/calendar.py` | OAuth, private Calendar persistence, payload conversion, Google API client. |
| `bot/retention.py` | Expired voice/text record cleanup. |
| `bot/logging_config.py` | Chisinau-time console/file logging. |
| `bot/handlers/start.py` | Start/help commands. |
| `bot/handlers/admin.py` | Roles, user list, user deletion. |
| `bot/handlers/calendar.py` | Calendar commands and OAuth callback HTTP route. |
| `bot/handlers/voice.py` | Voice/text ingestion, draft UI, Calendar confirmation/editor flow. |
| `bot/test_bot.py`, `bot/test_calendar.py` | Production unit tests. |
| `benchmarks/asr/` | Private-corpus ASR runners, Docker model images, scoring, docs. |
| `benchmarks/llm/` | Extraction contract fixtures, scoring, semantic and holdout benchmark tools. |
| `.github/workflows/ci.yml` | Test, compile, whitespace, and Docker-build CI checks. |
| `.github/workflows/codeql.yml` | Weekly and pull-request Python CodeQL scan. |
| `.github/dependabot.yml` | Monthly grouped dependency and Docker image updates. |
| `requirements.in` | Direct runtime dependency constraints. |
| `requirements.txt` | Generated hash-checked production lock file. |
| `.env.example` | Complete runtime configuration template. |
| `GOOGLE_CALENDAR_PLAN.md` | Implemented Calendar design record. |
| `NEXT_BRANCH_PLAN.md` | Current sequenced roadmap and out-of-scope rules. |

## Runtime

`bot.main.main()` sets `umask(0o077)`, loads config, configures logging, and runs `main_async()`.

`main_async()`:

1. Creates `Storage`, voice, and text roots.
2. Registers `AllowlistMiddleware` for messages and callback queries.
3. Registers routers in order: admin, Calendar, start, voice.
4. Starts retention loops for voice and text records.
5. Starts `aiohttp` callback listener on `0.0.0.0:$CALENDAR_CALLBACK_PORT` only if all Google OAuth config is present.
6. Starts aiogram polling with shared in-memory `drafts` and `calendar_edits` maps.

Drafts and edit state are process-local. Restarting bot clears them. Durable records and Calendar writes live under `data/`.

## User Interfaces

Allowed users can use `/start`, `/help`, `/connect_calendar`, `/disconnect_calendar`, plain text, and Telegram voice.

Admins also use `/admin_help`, `/list_users`, `/adduser <user_id>`. Owner alone can use `/add_admin <user_id>` and `/rm_admin <user_id>`.

Voice constraints:

- Telegram OGG/Opus input.
- Reject audio over 2 MiB.
- Reject voice replies while a draft clarification or Calendar text edit is active.
- Store transcript and record before showing response.

Text routing order matters:

1. Pending Calendar edit consumes text.
2. Pending semantic draft consumes text as strict answer.
3. Otherwise text is a new extraction request.

Do not add another text-consuming user state without resolving collision with drafts and Calendar edits.

## Semantic Contract and Safety

`bot/semantic.py` is source of truth for supported semantics. Model prompt/schema accepts only `create` or `unknown` operation plus typed date, time, end time, duration, location, recurrence, reminders, and correction fields.

Rules:

- Model copies source spans; it does not calculate dates/times or normalize text.
- `semantic.resolve()` validates shape/ranges and checks source grounding for extracted evidence fields such as title, location, duration, date, time, recurrence, and reminders. It does not independently ground every classification field.
- Resolver receives explicit local reference time and configured `ZoneInfo`.
- Named weekday always means next occurrence; same weekday means seven days later.
- Bare 1-12 clock values remain `unspecified` meridiem; resolver behavior must stay covered by tests.
- Resolver identifies invalid/nonexistent and ambiguous local times around DST.
- `trip` lasts until next midnight when duration is absent. Current trip fallback uses module-level Chisinau `ZONE`; account for this before claiming full non-default timezone support. Birthdays are all-day. Other types use `DEFAULT_DURATION` only after complete start exists.
- `auto_write` means `complete and not errors and not risks`; never loosen it casually.

Current risks include ungrounded fields, self-correction, location, recurrence, multiple reminders, past start, unknown operation, and DST ambiguity. New semantic capability needs a decision whether it adds an error, a risk, a confirmation path, and benchmark cases.

Clarifications are deterministic and must not invoke DeepSeek again:

- title: non-empty text;
- date: exact ISO `YYYY-MM-DD` or button;
- time: exact 24-hour `HH:MM`, `all day`, or button;
- six attempts maximum.

When changing prompt, schema, resolver, defaults, grounding, or risk behavior, update relevant tests and evaluate benchmark impact. `semantic.contract_hash()` is stored in request records for provenance.

## Calendar Contract

Calendar OAuth is per Telegram user. Scope is `calendar.events.owned` only. OAuth uses authorization code plus PKCE S256.

- State is random, private, single-use, and expires after ten minutes.
- Each user has durable `connection_generation`.
- Connecting, reconnecting, or disconnecting advances generation.
- Callback verifies user still has access and generation matches before saving token.
- Writes record generation. Any changed connection rejects old write before token refresh or Google insertion.
- Never log, show, or persist OAuth authorization codes. Keep OAuth client/tunnel secrets only in private `.env`; encrypt access/refresh token files with `CALENDAR_TOKEN_ENCRYPTION_KEY`; state files contain state metadata and PKCE verifier only. Existing plaintext token files migrate on their next read.

`build_event()` owns Google payload conversion:

- All-day end date is exclusive, start plus one day.
- Timed duration crosses DST through UTC elapsed time.
- Explicit empty reminders are not currently modeled; `reminders_minutes=[]` means inherit Google defaults.
- Google accepts at most five popup reminders, each 0 through 40,320 minutes.
- Recurrence becomes RFC 5545 `RRULE`; recurring events need timezone fields.

Every Calendar write is durable before request:

```text
pending -> shadowed | creating -> created | failed | cancelled
```

Write record keeps opaque `write_id`, stable Google event ID, payload fingerprint, source record, user, and connection generation. Stable Google event ID plus ownership marker makes retry conflict-safe. Do not substitute message IDs for write IDs in callback data.

`ACTIVE_WRITES` only prevents duplicate work in current process. It is not a crash-recovery lease. Stale `creating` recovery is intentionally planned in `NEXT_BRANCH_PLAN.md`; do not claim it already exists.

Runtime behavior:

- OAuth not configured: skip durable Calendar persistence/insertion path. OAuth configured but user disconnected: show connect action.
- `CALENDAR_WRITE_ENABLED=false`: persist `shadowed`; never call Google.
- Eligible safe write with enabled Calendar: create immediately.
- Risky complete write: show Confirm/Edit/Cancel.
- Existing editor changes only pending title, date, or time. Do not broaden editor/recurrence behavior without scoped product work.

## Authorization and Persistence

`AllowlistMiddleware` is global. Owner comes from `OWNER_ID`; admins/users from `data/access.json`. Unauthorized users are denied before handlers run.

`Storage` is single-process JSON persistence. Preserve atomic write (`tempfile.mkstemp` then `os.replace`) and private modes.

Private runtime layout:

```text
data/access.json
data/voice/<telegram_user_id>/<message_id>/audio.ogg
data/voice/<telegram_user_id>/<message_id>/record.json
data/text/<telegram_user_id>/<message_id>/record.json
data/google-calendar/tokens/<telegram_user_id>.json
data/google-calendar/states/<state>.json
data/google-calendar/connections/<telegram_user_id>.json
data/google-calendar/writes/<write_id>.json
logs/bot_DD_MM_YY.log
```

Directories use mode `0700`; private files use `0600`. Preserve this for new data paths. Voice/text record retention reads `expires_at` from `voice/*/*/record.json` or `text/*/*/record.json`, runs at startup then configured interval, and deletes each record directory. `data/voice-corpus/` is outside scan by design.

Removing a user calls `forget_user()`: it disconnects Calendar and removes that user's Calendar write records. Do not bypass this path.

## Configuration

Required values: `BOT_TOKEN`, `OWNER_ID`, `ELEVENLABS_API`, `DEEPSEEK_API`.

Defaults:

```text
DATA_DIR=data                     LOG_DIR=logs
LOG_LEVEL=INFO                    LOG_RETENTION_DAYS=7
VOICE_RETENTION_HOURS=168         VOICE_CLEANUP_INTERVAL_SECONDS=3600
ELEVENLABS_MODEL=scribe_v2        EXTRACTION_MODEL=deepseek-flash
EXTRACTION_TIMEOUT=60             DEBUG=false
CALENDAR_WRITE_ENABLED=false      CALENDAR_CALLBACK_PORT=8080
CALENDAR_TIMEZONE=Europe/Chisinau
```

OAuth values are required only when `CALENDAR_WRITE_ENABLED=true`; callback server can still start when OAuth credentials exist but writes remain disabled for shadow review. Whenever all OAuth values are configured, `CALENDAR_TOKEN_ENCRYPTION_KEY` must be a valid Fernet key held only in private `.env`.

`HOST_UID`, `HOST_GID`, and `CALENDAR_TUNNEL_TOKEN` are Compose deployment values. Keep exact variable names. Runtime uses `ELEVENLABS_API`; ASR cloud benchmark currently separately expects misspelled legacy `ELLEVENLABS_API`.

## Development and Verification

Install runtime dependencies from `requirements.txt` with hashes enforced. `requirements.in` owns direct constraints; `requirements.txt` is generated with Python 3.12 and `pip-tools==7.6.1`:

```bash
python -m pip install pip-tools==7.6.1
pip-compile --generate-hashes --no-header --output-file requirements.txt requirements.in
```

Do not hand-edit `requirements.txt`. No Makefile, formatter, linter, or type-check configuration exists. Follow existing standard-library-first, typed Python, `dataclass`, module-private helper, `unittest`, and `unittest.mock` style. Avoid dependencies unless existing standard library or installed package cannot solve concrete need.

Required checks before completing code changes:

```bash
python -m unittest discover -v
python -m unittest benchmarks.llm.test_semantic -v
python -m compileall -q bot benchmarks
git diff --check
docker build --tag voice-calendar-bot:ci .
```

CI runs same commands plus Docker build on pushes and pull requests. CodeQL scans Python on pull requests, `main` pushes, and weekly. Dependabot groups monthly `pip` and GitHub Actions version updates, independently opens security-update PRs, and tracks Docker image updates.

Targeted tests:

```bash
python -m unittest bot.test_bot bot.test_calendar -v
python -m unittest benchmarks.asr.test_cloud_runner -v
python -m unittest benchmarks.llm.test_resolve benchmarks.llm.test_semantic -v
```

No test may call live external services. Mock `urllib`, `aiohttp`, bot methods, and time as existing tests do.

## Benchmarks

ASR benchmark:

- `benchmarks/asr/run.py` builds Docker images, expects private corpus under `data/voice`, runs local models, and writes ignored result files.
- `benchmarks/asr/cloud_runner.py` runs Gemini, Gemini Live, AssemblyAI, or ElevenLabs against private corpus.
- `benchmarks/asr/semantic_judge.py` performs semantic comparison on prepared results.
- Public Markdown documents methodology/results; recordings and raw result artifacts stay private.

LLM benchmark:

- `benchmarks/llm/run.py` compares hosted extraction models against frozen corpus.
- `semantic_benchmark.py` runs/replays development semantic benchmark; use `--report-only` when API calls are not intended.
- `holdout_benchmark.py` protects frozen holdout. Default run is one-time; use `--report-only` for existing results. Do not rerun exposed holdout as fresh evaluation.
- Fixtures such as gold JSONL, manifests, frozen contract, and adjudications are evaluation assets, not casual test data.

Benchmark scripts need private `data/` inputs and provider keys. Do not run expensive model commands merely to validate ordinary code changes.

## Deployment

Docker image uses a reviewed digest of `python:3.12-slim`, installs the hash-checked `requirements.txt`, copies only `bot/`, creates `/data` and `/logs`, then runs as UID/GID `1000` by default. `docker-compose.yml` bind-mounts state/logs, sets no-new-privileges, and has optional `calendar` Cloudflare tunnel profile pinned to a reviewed digest.

Production deployment is manual. `DEPLOY.md` is ignored and local-only despite being present in this working tree; treat host details as sensitive operational information. No automatic deployment, release-image publishing, or health endpoint exists yet. Do not add GHCR release publishing, SBOM/provenance attestation, or CD until Branch 3 deployment-safety prerequisites are complete.

## GitHub Controls

`main` requires current passing `test` CI, enforces rules for admins, and blocks force pushes and deletion. GitHub Actions use read-only permissions unless a job needs more. Repository settings require full commit SHA action pins and enable Dependabot security updates, secret scanning, and secret-scanning push protection. Keep third-party actions SHA-pinned with a version comment so Dependabot can update them.

Do not add mandatory reviews or automatic deployment for this single-maintainer repository without explicit approval. GitHub Container Registry publication will be public and release-triggered only after Branch 3; deployed hosts must pull a reviewed immutable digest, never build mutable source on-host.

## Documentation and Git Hygiene

`.env`, `.venv/`, Python bytecode, `.DS_Store`, `data/`, `logs/`, and `DEPLOY.md` are ignored. Docker build also excludes env files, git metadata, private data/logs, and selected docs.

Do not commit generated caches, private audio, raw transcripts, API output containing private data, tokens, or deployment secrets. `README.md` is user/operator guide; update it when commands, configuration, user behavior, safety rules, or deployment steps change. Keep `AGENTS.md` current when architecture or contributor workflow changes.

Current untracked planning files may belong to user work. Do not delete, stage, or overwrite them unless explicitly asked.

## Roadmap Boundaries

`NEXT_BRANCH_PLAN.md` defines three committed branches:

1. Calendar status, account identity, token recovery, stale creating-write recovery.
2. `/settings`: per-user auto-write opt-in, timezone, built-in type-duration overrides, and automatic custom types with durations. Automatic creation must require global enablement, user opt-in, and resolver `auto_write=true`.
3. Deployment safety: health check, backups/rollback, manual deployment before automation.

Deferred separate product branches: editor additions one field at a time; Google Maps with Moldova-biased location search, formatted address/preview, and `LocationChoice` for ambiguous results. Do not fold Maps into recovery/preferences/editor work. Other work requiring separate approval: event listing, general natural-language editing, recurrence mutation, broad settings search, arbitrary scripts, and category-specific LLM prompts.
