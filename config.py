"""Sozlamalar: .env fayldan o'qiladi."""
import os
from datetime import timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(BASE_DIR / ".env")


def _int(name, default):
    return int(os.environ.get(name) or default)


# HolliHop
HH_DOMAIN = os.environ.get("HH_DOMAIN", "")
HH_API_KEY = os.environ.get("HH_API_KEY", "")
GROUP_TYPES = os.environ.get("GROUP_TYPES", "Group")

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"  # 1 = Telegramga yubormay, ekranga chiqarish

# Vaqt (O'zbekiston UTC+5)
TZ = timezone(timedelta(hours=_int("TZ_OFFSET_HOURS", 5)))
POLL_MINUTES = _int("POLL_MINUTES", 10)
DIGEST_TIMES = [t.strip() for t in os.environ.get("DIGEST_TIMES", "12:00,17:00").split(",") if t.strip()]
DAILY_REPORT_TIME = os.environ.get("DAILY_REPORT_TIME", "18:00")
WEEKLY_REPORT_DAY = _int("WEEKLY_REPORT_DAY", 5)  # 0=Dushanba ... 5=Shanba
WEEKLY_REPORT_TIME = os.environ.get("WEEKLY_REPORT_TIME", "17:00")
ABSENCE_DEADLINE = os.environ.get("ABSENCE_DEADLINE", "20:00")
# Shu vaqtdan keyin boshlangan darslarda kelmaganlar ertasi kuni 12:00 da tekshiriladi
LATE_LESSON_FROM = os.environ.get("LATE_LESSON_FROM", "18:00")

# Qoidalar
COURSE_MIN_DAYS = _int("COURSE_MIN_DAYS", 350)  # kurs 1 yil (15 kunlik farqqa ruxsat)
MAX_LESSONS_FOR_NEW_STUDENT = _int("MAX_LESSONS_FOR_NEW_STUDENT", 3)

DB_PATH = BASE_DIR / "audit.db"
LOG_PATH = BASE_DIR / "audit.log"
