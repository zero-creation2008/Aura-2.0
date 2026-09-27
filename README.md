# DIVI 0.1 — Termux personal automation core

DIVI is a deterministic assistant. It uses no AI API and no LLM. The working slice includes a local command parser, SQLite notes/memory/reminders, Termux battery, bounded web fetch, audit, verified backup, owner login, dashboard, and an authenticated WebSocket connection. Unrecognized requests are reported as unknown.

## Install on Termux

1. Copy the project directory onto the phone, for example as `~/storage/downloads/divi` after extracting the ZIP.
2. Run:

```sh
cd ~/storage/downloads/divi
bash install.sh
divi owner setup
divi start
```

Use a password of at least 12 characters. Visit `http://127.0.0.1:8737` in the phone browser. The Termux:API **Android app** is additionally required for battery information; `pkg install termux-api` installs its Termux commands. For reminders, allow notifications for Termux:API.

```sh
divi status
divi health
divi selftest
divi battery
divi 'save note physics exam Monday'
divi 'show notes'
divi 'remind me at 8 pm to study physics'
divi 'remember project directory is ~/Projects'
divi 'what is my project directory'
divi 'search internet for Termux documentation'
divi fetch https://example.com
divi backup
divi history
divi devices list
divi devices revoke DEVICE_ID
divi alias device.battery 'power level'
divi logs
divi stop
```

The reminder scheduler is active while the server runs. Time expressions use the phone's time zone. Backups contain the SQLite database and default config; the owner password file and any future vault are excluded. Keep backups private because notes and memory are included.

## Remote browser access

DIVI binds to `127.0.0.1`. To reach it from another network, install a **trusted outbound HTTPS tunnel** on the Termux phone and point it at `http://127.0.0.1:8737`. A Cloudflare Quick Tunnel can be used for temporary testing; its generated address changes and the provider says it is for development only. For a stable address, create a named tunnel and hostname in the provider's dashboard, then configure that exact HTTPS address as `server.publicOrigin` in `config/default.json`, restart DIVI, and run its tunnel connector. `publicOrigin` must be the full origin, such as `https://divi.example.com`, without a trailing slash. This sets Secure cookies and checks the browser WebSocket Origin. Do not forward raw port 8737 from a router. The tunnel must provide TLS at the public endpoint. A hostname and provider account are outside this ZIP. Android battery management may stop Termux in the background; verify persistence on the actual phone.

Once the tunnel is active: open the HTTPS URL from a separate device, sign in with the owner password, issue `battery`, and inspect `divi history` locally. This end-to-end test has **not** been run on a real phone or a second network in this build.

## Security and precise scope

- Password hash: scrypt with random salt. Sessions: random 256-bit tokens, stored hashed, 24-hour expiry, HttpOnly and SameSite=Strict cookies; Secure cookie with configured public HTTPS origin. POST routes require CSRF token. Five failed logins per IP per 15 minutes triggers a temporary lock. Logout and device revocation remove server sessions.
- Audits log intent and outcome, without command text or secret values. SQLite audit keeps the newest 10,000 records. No arbitrary shell from user input. Deletion of a note/fact asks for browser confirmation.
- Internet fetch defaults to HTTPS, blocks known local/private addresses and redirects, limits response size and has a timeout. DNS rebinding and complex proxy setups need additional hardening before broad exposure of fetch to untrusted clients. Search gives a browser URL; it does **not** scrape and rank results.
- This release **does not implement** downloader, file manager, vault, general event engine, recurring scheduler, offline voice, watchdog, self-healing, signed versioned updater, automatic code changes, WebSocket event push, full remote device verification, or full Jarvis features. `divi update` reports unavailable. Do not enable `updates.automaticApply`. A tunnel is not bundled or configured automatically. The core can be expanded with independently reviewed tools and explicit permissions.
- There is one owner account and every successful login creates a device entry. Browser sessions are stored in SQLite; the browser CSRF token is held in sessionStorage. Device naming is self-declared, so password possession still determines trust. Remote file access is not enabled.

## Structure

`src/core.py`: parser, registered tool dispatch, SQLite, network policy, audit, backup. `src/server.py`: localhost REST/WebSocket dashboard, authentication, reminder loop. `bin/divi`: CLI lifecycle. `tests/`: executable checks. Persistent data goes in `data/`, private login material in `secure/`, and backups in `backups/`; these are ignored by Git.
