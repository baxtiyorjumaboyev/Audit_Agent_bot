"""Telegramga xabar yuborish."""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config

log = logging.getLogger("telegram")
LIMIT = 3900


def _api(method, token=None, **params):
    url = f"https://api.telegram.org/bot{token or config.TELEGRAM_BOT_TOKEN}/{method}"
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


def send(text, chat_id=None, thread_id=None, token=None):
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if config.DRY_RUN:
        print(f"\n----- TELEGRAM (DRY_RUN) → {chat_id} / {thread_id} -----\n" + text + "\n------------------------------")
        return
    extra = {"message_thread_id": thread_id} if thread_id else {}
    for part in _chunks(text):
        _api("sendMessage", token, chat_id=chat_id, text=part, parse_mode="HTML", disable_web_page_preview="true", **extra)
    log.info("Telegramga yuborildi → %s/%s (%d belgi)", chat_id, thread_id or "-", len(text))


def send_errors(text):
    """Xatoliklar: RND bot orqali "Robbit | TEAM" guruhining General mavzusiga (sozlanmagan bo'lsa asosiy kanalga)."""
    if config.TEAM_BOT_TOKEN and config.TEAM_GROUP_ID:
        send(text, chat_id=config.TEAM_GROUP_ID, token=config.TEAM_BOT_TOKEN)
    else:
        send(text)


def send_to_branches(items, build):
    """Har bir filial xatolarini "Robbit | TEAM" guruhidagi o'sha filial mavzusiga yuboradi.
    items: office maydoni bor yozuvlar; build(office, office_items) -> matn."""
    if not config.TEAM_CHAT_ID:
        return
    by_office = {}
    for item in items:
        by_office.setdefault(item["office"], []).append(item)
    for office, office_items in by_office.items():
        thread = config.BRANCH_TOPICS.get(office)
        if thread is None:
            log.info("%s filiali uchun mavzu sozlanmagan, yuborilmadi", office)
            continue
        try:
            send(build(office, office_items), chat_id=config.TEAM_CHAT_ID, thread_id=thread,
                 token=config.TEAM_BOT_TOKEN or None)
        except Exception:
            # Asosiy kanalga allaqachon ketgan, shuning uchun ish qayta bajarilmaydi
            log.exception("%s filiali mavzusiga yuborib bo'lmadi", office)


def find_chats(token=None):
    """Bot qo'shilgan kanal/guruhlar ID'sini topish uchun."""
    chats = {}
    if _api("getWebhookInfo", token).get("result", {}).get("url"):
        raise RuntimeError("Botga webhook ulangan (boshqa tizim ishlatyapti), getUpdates bilan tekshirib bo'lmaydi.")
    for upd in _api("getUpdates", token).get("result", []):
        for key in ("message", "channel_post", "my_chat_member", "edited_channel_post"):
            chat = (upd.get(key) or {}).get("chat")
            if chat:
                chats[chat["id"]] = chat.get("title") or chat.get("username") or ""
    return chats
