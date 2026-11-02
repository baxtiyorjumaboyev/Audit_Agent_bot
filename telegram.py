"""Telegramga xabar yuborish."""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config

log = logging.getLogger("telegram")
LIMIT = 3900


def _api(method, **params):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram {method}: {e.code} {e.read().decode('utf-8', 'replace')}")


def _chunks(text):
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > LIMIT and chunk:
            yield chunk
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        yield chunk


def send(text):
    if config.DRY_RUN:
        print("\n----- TELEGRAM (DRY_RUN) -----\n" + text + "\n------------------------------")
        return
    for part in _chunks(text):
        _api("sendMessage", chat_id=config.TELEGRAM_CHAT_ID, text=part,
             parse_mode="HTML", disable_web_page_preview="true")
    log.info("Telegramga yuborildi (%d belgi)", len(text))


def find_chats():
    """Bot qo'shilgan kanal/guruhlar ID'sini topish uchun."""
    chats = {}
    for upd in _api("getUpdates").get("result", []):
        for key in ("message", "channel_post", "my_chat_member", "edited_channel_post"):
            chat = (upd.get(key) or {}).get("chat")
            if chat:
                chats[chat["id"]] = chat.get("title") or chat.get("username") or ""
    return chats
