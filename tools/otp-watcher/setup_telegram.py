#!/usr/bin/env python3
"""Safely connect an existing BotFather bot to GitHub Actions secrets.

Does not create Telegram bots or request Gmail credentials.
Requires GitHub CLI (gh), an authenticated GitHub account, and a bot created at @BotFather.
"""
import getpass
import json
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen

REPOSITORY = "zero-creation2008/Aura-2.0"


def telegram_call(token, endpoint, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")
    request = Request(
        "https://api.telegram.org/bot" + token + "/" + endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
    except Exception:
        # Never expose the token embedded in the API URL through an exception.
        raise RuntimeError("Telegram API request failed; check the bot token, network, and webhook.") from None
    if not result.get("ok"):
        raise RuntimeError("Telegram API returned an error; verify the bot permissions.")
    return result["result"]


def save_secret(name, value):
    # gh secret set with stdin avoids placing the secret in CLI argv or shell history.
    outcome = subprocess.run(
        ["gh", "secret", "set", name, "--repo", REPOSITORY],
        input=value + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if outcome.returncode:
        raise RuntimeError("Could not set GitHub secret " + name + "; run gh auth login and verify repo write access.")


def main():
    print("Gmail OTP Watcher | Telegram bot setup")
    print("Create a new bot privately with @BotFather (/newbot) before using this helper.")
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI not found. Install gh and run gh auth login first.")
    check = subprocess.run(["gh", "auth", "status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        raise RuntimeError("GitHub CLI is not authenticated. Run gh auth login first.")

    token = getpass.getpass("Enter YOUR BotFather token (hidden): ").strip()
    if not token or any(x.isspace() for x in token):
        raise RuntimeError("Invalid empty token.")
    me = telegram_call(token, "getMe")
    print("Bot confirmed: @" + str(me.get("username") or "(unnamed)"))

    print("Open https://t.me/" + str(me.get("username")) + " on YOUR Telegram, tap Start / send /start.")
    input("Press Enter after sending /start ... ")
    try:
        updates = telegram_call(token, "getUpdates", {"limit": 100, "timeout": 0})
    except RuntimeError:
        updates = []
        print("Cannot read bot updates (possibly existing webhook). Enter your private chat ID manually.")

    chats = {}
    for item in updates:
        message = item.get("message", {})
        chat = message.get("chat", {})
        if chat.get("type") == "private" and isinstance(chat.get("id"), int):
            chats[chat["id"]] = chat.get("username") or chat.get("first_name") or "Private chat"
    if chats:
        items = list(chats.items())
        for i, (cid, label) in enumerate(items, 1):
            print(f"{i}. {label} (chat ID: {cid})")
        answer = input("Choose the chat number belonging to YOU (or type m for manual): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            chat_id = str(items[int(answer) - 1][0])
        else:
            chat_id = input("Enter your own private chat ID: ").strip()
    else:
        chat_id = input("Enter your private chat ID (from getUpdates; no bot/group IDs): ").strip()

    if not chat_id.lstrip("-").isdigit():
        raise RuntimeError("Chat ID must be a numeric Telegram chat ID.")
    if not chat_id.isdigit():
        raise RuntimeError("Only private positive chat IDs are supported.")

    print("Ready to save TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to GitHub Actions secrets.")
    if input("Confirm secrets upload to " + REPOSITORY + " [y/N]: ").strip().lower() != "y":
        print("Cancelled; no secrets were uploaded.")
        return
    save_secret("TELEGRAM_BOT_TOKEN", token)
    save_secret("TELEGRAM_CHAT_ID", chat_id)
    print("Telegram secrets saved. No tokens displayed or committed.")

    if input("Send one harmless bot test message now? [y/N]: ").strip().lower() == "y":
        telegram_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "OTP Watcher setup verified. Real OTP codes are never forwarded to Telegram.",
            "disable_web_page_preview": True,
        })
        print("Test accepted by Telegram.")
    print("NEXT: Configure GMAIL_TOKEN_JSON as a GitHub Actions secret and merge PR #1.")
    print("GitHub Actions is best-effort (~5 minute checks), not an instant 24/7 server.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print("Setup failed:", error, file=sys.stderr)
        sys.exit(1)
