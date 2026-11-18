#!/usr/bin/env python3
"""Read-only Gmail verification-email watcher; no secrets sent to cloud notifications."""
import argparse
import base64
from email.utils import parseaddr
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from urllib.parse import quote
from urllib.request import Request as UrlRequest, urlopen


SCOPE = ["https://www.googleapis.com/auth/gmail.readonly"]
QUERY = 'in:inbox newer_than:1d {otp "verification code" "security code" "one-time password" "one time password" "login code" "your code" passcode "authentication code"}'
KEYWORDS = re.compile(r'(?i)\b(otp|verification|security code|one.time pass(word|code)|login code|your code|passcode|authentication code|2.fa)\b')
CODE = re.compile(r'(?i)(?:otp|verification(?:\s+code)?|security\s+code|login\s+code|passcode|authentication\s+code|your\s+code|code\s+is)\D{0,45}(?<!\d)(\d{4,8})(?!\d)')
SERVICE_NAMES = {"instagram": "Instagram", "facebook": "Facebook", "meta.com": "Meta", "google": "Google", "microsoft": "Microsoft", "github": "GitHub", "discord": "Discord", "telegram": "Telegram", "whatsapp": "WhatsApp", "freefire": "Free Fire", "garena": "Garena", "xiaomi": "Xiaomi", "deepseek": "DeepSeek", "openai": "OpenAI", "bank": "Banking"}


def extract_code(subject, snippet, body=""):
    # Restrict to OTP-themed messages; numbers alone are not proof of an OTP.
    text = "\n".join((subject, snippet, body[:2500]))
    if not KEYWORDS.search(text):
        return None
    match = CODE.search(text)
    if match:
        return match.group(1)
    candidates = re.findall(r'(?<!\d)\d{6}(?!\d)', "\n".join((subject, snippet)))
    return candidates[0] if len(candidates) == 1 else None


def service_name(sender, subject):
    addr = parseaddr(sender)[1].lower()
    haystack = (addr + " " + subject.lower())
    for name, label in SERVICE_NAMES.items():
        if name in haystack:
            return label
    return addr.split("@")[-1] if "@" in addr else "Unknown service"


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    token_json = os.getenv("GMAIL_TOKEN_JSON", "").strip()
    token_path = Path(os.getenv("GMAIL_TOKEN_PATH", "token.json"))
    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPE)
    elif token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPE)
    else:
        client_path = Path(os.getenv("GMAIL_CLIENT_PATH", "credentials.json"))
        if not client_path.exists():
            raise RuntimeError("Missing credentials.json; see README for Google OAuth setup")
        if os.getenv("GITHUB_ACTIONS"):
            raise RuntimeError("No OAuth token: configure GMAIL_TOKEN_JSON GitHub secret")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPE)
        creds = flow.run_local_server(port=0)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds.valid:
        raise RuntimeError("Gmail OAuth credentials invalid; reconnect locally")
    if not token_json:
        token_path.write_text(creds.to_json(), encoding="utf-8")
        os.chmod(token_path, 0o600)
    return creds


def db_connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, checked_at INTEGER NOT NULL)")
    db.commit()
    return db


def already_seen(db, message_id):
    return db.execute("SELECT 1 FROM seen WHERE id=?", (message_id,)).fetchone() is not None


def remember(db, message_id):
    db.execute("INSERT OR IGNORE INTO seen (id, checked_at) VALUES (?, ?)", (message_id, int(time.time())))
    db.commit()


def decode_body(payload):
    texts = []
    def walk(part):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                try:
                    texts.append(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")[:3000])
                except (ValueError, UnicodeError):
                    pass
        for child in part.get("parts", []):
            walk(child)
    walk(payload)
    return "\n".join(texts)[:3000]


def message_link(thread_id):
    # Thread IDs are provider-returned strings, not email supplied URLs.
    return "https://mail.google.com/mail/u/0/#all/" + quote(thread_id, safe="")


def notify_local(service, code, link, display_code=False):
    title = f"Verification email — {service}"
    message = f"OTP: {code}" if display_code and code else "Open Gmail to view your OTP"
    # Don't expose codes in scripts/logs or third-party cloud messaging.
    cmd = ["termux-notification", "--title", title, "--content", message,
           "--priority", "high"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def send_telegram_message(text):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    payload = json.dumps({"chat_id": chat_id,
                          "text": text,
                          "disable_web_page_preview": True}).encode()
    req = UrlRequest("https://api.telegram.org/bot" + bot_token + "/sendMessage",
                     data=payload, headers={"Content-Type": "application/json"}, method="POST")
    # Never print URLs, tokens, or response bodies in exception messages.
    try:
        with urlopen(req, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError("Telegram notification failed")
    except Exception:
        raise RuntimeError("Telegram notification failed; check token, chat ID, and network") from None


def notify_telegram(service, link):
    # Do not put OTPs, passcodes, message snippets, or email bodies in Telegram.
    send_telegram_message(f"Verification email received from {service}. Open Gmail to view it:\n{link}")


def scan(gmail, db, mode, show_code=False, max_age_seconds=900):
    now_ms = int(time.time() * 1000)
    page = None
    sent = 0
    while True:
        result = gmail.users().messages().list(userId="me", q=QUERY, maxResults=100,
                                                pageToken=page).execute()
        for item in result.get("messages", []):
            mid = item["id"]
            if already_seen(db, mid):
                continue
            # Only fetch message metadata/body from your own authorized inbox.
            msg = gmail.users().messages().get(userId="me", id=mid,
                                                format="full" if mode == "termux" else "metadata").execute()
            age_ms = now_ms - int(msg.get("internalDate", 0))
            if age_ms < 0:
                continue
            if age_ms > max_age_seconds * 1000:
                remember(db, mid)
                continue
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("subject", "")
            sender = headers.get("from", "")
            snippet = msg.get("snippet", "")
            # Gmail search is a candidate filter; metadata/body must still match OTP terms.
            body = decode_body(msg.get("payload", {})) if mode == "termux" else ""
            if not KEYWORDS.search(" ".join((subject, snippet, body[:2000]))):
                remember(db, mid)
                continue
            service = service_name(sender, subject)
            link = message_link(msg.get("threadId", mid))
            if mode == "termux":
                otp = extract_code(subject, snippet, body)
                notify_local(service, otp, link, display_code=show_code)
            else:
                notify_telegram(service, link)
            remember(db, mid)
            sent += 1
        page = result.get("nextPageToken")
        if not page:
            break
    db.execute("DELETE FROM seen WHERE checked_at < ?", (int(time.time()) - 7 * 86400,))
    db.commit()
    return sent


def main():
    p = argparse.ArgumentParser(description="Personal Gmail OTP watcher; Gmail is never modified")
    p.add_argument("--mode", choices=["termux", "github"], default="termux")
    p.add_argument("--interval", type=int, default=30, help="Termux polling interval in seconds")
    p.add_argument("--show-code", action="store_true", help="LOCAL ONLY: include OTP in Android notification")
    p.add_argument("--once", action="store_true")
    p.add_argument("--telegram-test", action="store_true", help="Send a harmless test message to Telegram; no Gmail access")
    p.add_argument("--state", default=".state/seen.sqlite3")
    a = p.parse_args()
    if a.mode == "github" and a.show_code:
        p.error("OTP codes must not be sent from GitHub runner")
    if a.mode == "termux" and a.interval < 15:
        p.error("minimum supported interval: 15 seconds")
    if a.show_code:
        print("Warning: phone notifications can reveal verification codes on the lock screen.")
    if a.telegram_test:
        if a.mode != "github":
            p.error("--telegram-test requires --mode github")
        send_telegram_message("OTP watcher test: Telegram notifications are configured. No OTP or Gmail content included.")
        print("Telegram test notification sent (no OTP or Gmail access).")
        return
    from googleapiclient.discovery import build
    gmail = build("gmail", "v1", credentials=credentials(), cache_discovery=False)
    db = db_connect(a.state)
    try:
        while True:
            n = scan(gmail, db, a.mode, show_code=a.show_code)
            print(f"Scan completed: {n} new verification notifications (codes never logged).", flush=True)
            if a.once or a.mode == "github":
                break
            time.sleep(a.interval)
    finally:
        db.close()


if __name__ == "__main__":
    main()
