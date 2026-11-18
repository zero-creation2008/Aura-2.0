# Gmail OTP Watcher (Personal, Read-Only)

Monitor your own Gmail for verification emails and show which service sent them. Optional Telegram alerts use the official Telegram Bot API; they do not require a third-party Telegram connector. All Gmail access is **read-only**; this script never marks messages read, sends Gmail messages, or archives/deletes anything.

## Two modes

| Mode | Runs where | Frequency | Notification | OTP contents |
|---|---|---|---|---|
| Termux | Your own Android phone | Every ~30 seconds while alive | Android local notification | Optional `--show-code` flag (lockscreen privacy risk) |
| GitHub Actions | GitHub-hosted short jobs | Scheduled ~5-minute intervals, can be late/missed | Telegram bot message with service + Gmail link | **Never sent** to Telegram or logs |

GitHub is a code host, **not a 24/7 always-on process host**. For urgent expiring OTPs use Gmail's native push notifications, or Termux local mode while your phone is running. Neither Termux nor GitHub Actions guarantees instant notification.

## Google OAuth setup (one-time)

1. In Google Cloud Console, create a project, enable **Gmail API**, configure OAuth consent screen, then create an **OAuth desktop application** client.
2. Download client JSON as `credentials.json` into this directory. **Never commit it.** Use only your own mailbox.
3. On a desktop/laptop with browser support, run:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   python watcher.py --mode termux --once
   ```

   Approve only the read-only Gmail scope. This creates `token.json` with a refresh token (chmod 600). If you are not using Termux on the computer, local notification may fail on first run after OAuth; that is expected. You can generate the token using the Python OAuth flow, then run on Android. **Never upload `token.json` into the repo or screenshots.**
4. For local Termux, install **Termux** and **Termux:API** companion app from trusted matching distribution sources. Enable its notification permission. Copy `credentials.json` and `token.json` securely to the private working folder and install packages:

   ```bash
   pkg update && pkg install python termux-api -y
   python -m pip install -r requirements.txt
   chmod 600 credentials.json token.json
   termux-notification --title 'OTP watcher' --content 'Notifications ready'
   termux-wake-lock
   python watcher.py --mode termux --interval 30 --show-code
   ```

   `--show-code` sends extracted OTPs **only** to a local Android notification; hide notification content on your lock screen. Omit it to show the service and open Gmail instead. Android battery restrictions can kill long-running Termux processes; adjust Termux battery/background settings and install Termux:Boot if you need reboot startup. Native Gmail notifications are the most reliable for urgent codes.

## GitHub Actions scheduled mode

**Set these GitHub repository Actions secrets** under Settings → Secrets and variables → Actions:

- `GMAIL_TOKEN_JSON`: exact *contents* of locally created `token.json` (keep private; grants mailbox read access).
- `TELEGRAM_BOT_TOKEN`: token of a bot you own.
- `TELEGRAM_CHAT_ID`: your personal chat ID with that bot; send `/start` to the bot first.

To test Telegram delivery before connecting Gmail, run locally with `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set in your environment:

```bash
python watcher.py --mode github --telegram-test
```

Never paste bot tokens or Gmail OAuth JSON into a chat, issue, commit, or workflow file. **Set them as private GitHub Actions secrets only.** The bot must have an existing private chat with you (send `/start` to your bot). To find your own chat ID, query the Telegram Bot API `getUpdates` privately after starting the bot; do not post this response publicly.

Merge the feature branch to the repository default branch to activate `.github/workflows/otp-watch.yml`. Actions → `Personal Gmail verification alerts` → `Run workflow` to start a scan manually. Select **Send a harmless Telegram test alert** to confirm the bot works without granting Gmail access. Code never gets forwarded; cloud message only includes sender service and a Gmail link. The workflow uses a best-effort cache to suppress duplicates. Failed/missed runs can still cause delays or duplicates. GitHub Actions costs and minute limits apply.

> OAuth note: A Google OAuth application in **Testing** mode with a personal/external user may issue refresh tokens that expire after 7 days; account/app settings can affect long-term availability. If it expires, reauthorize locally and replace the GitHub secret.

## Security design

- Script uses `gmail.readonly`, *never* `gmail.modify`.
- `credentials.json`, `token.json`, `.env`, and `.state` are gitignored.
- Remote (GitHub/Telegram) mode does not transmit OTP codes.
- OTP messages are untrusted data. The tool uses only Google API metadata, never follows email links or instructions in messages.
- Codes are not printed to console logs. Avoid sharing workflows/logs that might expose sensitive configuration.
- SQLite stores only message IDs for duplicate suppression and automatically prunes old records.

## Quick tests

```bash
python3 -m compileall -q watcher.py
python3 -m unittest discover -s tests -v
```

> Connector availability: A dedicated “Telepath” connector was not available in this session. This code sends bot messages using the official Telegram Bot API instead.
