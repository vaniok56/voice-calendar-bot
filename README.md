# Telegram Bot Template

Minimal private aiogram bot template with owner-managed access, admin ranks, file logging, and Docker deployment.

Allowed users can send voice messages directly. The bot queues downloads, preserves Telegram's original OGG/Opus format, and replies with filename, size, and duration.

## Commands

- `/start` - verify bot is running.
- `/help` - show voice storage information.
- `/admin_help` - show administration commands.
- `/list_users` - open paginated user list with removal controls.
- `/adduser <user_id>` - add user.
- `/add_admin <user_id>` - promote user; owner only.
- `/rm_admin <user_id>` - demote admin; owner only.

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

Voice messages live in `data/voice` with private permissions and Chisinau timestamp filenames. Files older than seven days are removed at startup and hourly. Maximum voice size is 2 MiB (roughly 1 minute 40 seconds).

Set `HOST_UID` and `HOST_GID` in `.env` when bind-mounted directories belong to a user other than `1000:1000`.
