"""HolliHop Audit Agent.

Buyruqlar:
    python main.py            agentni doimiy ishga tushirish
    python main.py test       HolliHop va Telegram ulanishini tekshirish
    python main.py chat-id    bot qo'shilgan kanal ID'sini topish
    python main.py poll       bir marta tekshiruv o'tkazish
    python main.py digest | daily | weekly | absences   hisobotni hozir yuborish
"""
import logging
import sys
import time
from datetime import datetime, timedelta

import audit
import config
import reports
import telegram
from db import connect, get_meta, set_meta
from hh_api import HHClient, HHError

log = logging.getLogger("main")


def setup_logging():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(config.LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)])


def client():
    if not config.HH_DOMAIN or not config.HH_API_KEY:
        sys.exit(".env faylida HH_DOMAIN va HH_API_KEY ni to'ldiring.")
    return HHClient(config.HH_DOMAIN, config.HH_API_KEY)


def poll(conn, hh):
    baseline = get_meta(conn, "baseline_done") is None
    if baseline:
        log.info("Birinchi ishga tushish: hozirgi holat saqlanmoqda, xatolik yuborilmaydi.")
    audit.sync_groups(conn, hh, baseline)
    audit.sync_links(conn, hh, baseline)
    audit.sync_absences(conn, hh, audit.now().date())
    if baseline:
        set_meta(conn, "baseline_done", audit.now().isoformat(timespec="seconds"))
        telegram.send("🤖 <b>Audit agent ishga tushdi.</b>\nHolliHop'ning hozirgi holati saqlandi. "
                      "Bundan keyingi o'zgarishlar nazorat qilinadi.")


# ---------- Rejali ishlar ----------

def job_digest(conn, hh):
    t = audit.now()
    if t.strftime("%H:%M") < "13:00":
        # kecha kechqurun (LATE_LESSON_FROM dan keyin) bo'lgan darslarda kelmaganlar
        yesterday = t.date() - timedelta(days=1)
        audit.sync_absences(conn, hh, yesterday)
        rows = audit.uncontacted_absences(conn, yesterday, late=True)
        if rows:
            telegram.send(reports.absence_alert(rows, f"📵 Kecha kechki darsda kelmagan, bog'lanilmagan o'quvchilar ({audit.fmt_date(yesterday)})"))
            audit.mark_absences_alerted(conn, rows)
    events = conn.execute("SELECT * FROM events WHERE sent=0 ORDER BY ts").fetchall()
    telegram.send(reports.digest(events, f"⚠️ Admin xatoliklari: {t.strftime('%d.%m.%Y, %H:%M')}", t.date()))
    conn.executemany("UPDATE events SET sent=1 WHERE id=?", [(e["id"],) for e in events])
    conn.commit()


def job_daily(conn, hh):
    day = audit.now().date()
    events = conn.execute("SELECT * FROM events WHERE substr(ts, 1, 10)=? ORDER BY ts", (day.isoformat(),)).fetchall()
    absences = conn.execute("SELECT * FROM absences WHERE day=? AND pass=1", (day.isoformat(),)).fetchall()
    telegram.send(reports.daily(events, absences, day, config.ABSENCE_DEADLINE))


def job_absences(conn, hh):
    day = audit.now().date()
    audit.sync_absences(conn, hh, day)
    rows = audit.uncontacted_absences(conn, day, late=False)
    telegram.send(reports.absence_alert(rows, f"📵 Kelmagan o'quvchiga bog'lanilmadi: {audit.fmt_date(day)}, {config.ABSENCE_DEADLINE} holatiga"))
    audit.mark_absences_alerted(conn, rows)


def job_weekly(conn, hh):
    today = audit.now().date()
    start, end = reports.week_range(today)
    prev = start - timedelta(days=7)

    def events_between(a, b):
        return conn.execute("SELECT * FROM events WHERE substr(ts, 1, 10) BETWEEN ? AND ? ORDER BY ts",
                            (a.isoformat(), b.isoformat())).fetchall()

    absences = conn.execute("SELECT * FROM absences WHERE day BETWEEN ? AND ? AND pass=1",
                            (start.isoformat(), end.isoformat())).fetchall()
    teacher_groups = conn.execute("SELECT * FROM groups WHERE teacher_changes > 0 AND present=1 "
                                  "ORDER BY teacher_changes DESC").fetchall()
    telegram.send(reports.weekly(events_between(start, end), events_between(prev, start - timedelta(days=1)),
                                 absences, teacher_groups, start, end))


JOBS = ([(f"digest_{t}", t, None, job_digest) for t in config.DIGEST_TIMES]
        + [("daily", config.DAILY_REPORT_TIME, None, job_daily),
           ("weekly", config.WEEKLY_REPORT_TIME, config.WEEKLY_REPORT_DAY, job_weekly),
           ("absences", config.ABSENCE_DEADLINE, None, job_absences)])
# Bir vaqtga to'g'ri kelsa: avval xatoliklar, keyin hisobot
JOBS.sort(key=lambda j: (j[1], j[3] is job_weekly))


def due(conn, name, hhmm, weekday, t):
    if weekday is not None and t.weekday() != weekday:
        return False
    h, m = map(int, hhmm.split(":"))
    scheduled = t.replace(hour=h, minute=m, second=0, microsecond=0)
    if not (scheduled <= t < scheduled + timedelta(hours=2)):  # kompyuter o'chiq bo'lsa 2 soat ichida yetkaziladi
        return False
    return conn.execute("SELECT 1 FROM jobs WHERE name=? AND day=?", (name, t.date().isoformat())).fetchone() is None


def run():
    conn, hh = connect(), client()
    last_poll, failures = None, 0
    log.info("Audit agent ishga tushdi. Tekshiruv har %d daqiqada.", config.POLL_MINUTES)
    while True:
        t = audit.now()
        due_jobs = [j for j in JOBS if due(conn, j[0], j[1], j[2], t)]
        if last_poll is None or t - last_poll >= timedelta(minutes=config.POLL_MINUTES) or due_jobs:
            try:
                poll(conn, hh)
                failures = 0
            except Exception as e:
                failures += 1
                log.exception("Tekshiruvda xato: %s", e)
                if failures == 3:
                    try:
                        telegram.send(f"⚠️ Audit agent HolliHop'ga ulana olmayapti:\n<code>{e}</code>")
                    except Exception:
                        log.exception("Telegramga yuborib bo'lmadi")
            last_poll = t
        for name, hhmm, weekday, fn in due_jobs:
            try:
                fn(conn, hh)
                conn.execute("INSERT OR IGNORE INTO jobs VALUES(?, ?)", (name, t.date().isoformat()))
                conn.commit()
            except Exception:
                log.exception("%s bajarilmadi", name)
        time.sleep(20)


def test():
    hh = client()
    try:
        offices = hh.call("GetOffices").get("Offices") or []
        print(f"✅ HolliHop ulandi. Filiallar ({len(offices)}): " + ", ".join(o.get("Name", "") for o in offices))
        groups = hh.groups(config.GROUP_TYPES, "Working")
        print(f"✅ O'qiyotgan guruhlar: {len(groups)} ta")
    except HHError as e:
        print(f"❌ HolliHop: {e}")
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("❌ .env faylida TELEGRAM_BOT_TOKEN va TELEGRAM_CHAT_ID ni to'ldiring.")
        return
    try:
        telegram.send("✅ Audit agent: test xabari. Ulanish ishlayapti.")
        print("✅ Telegramga test xabari yuborildi.")
    except Exception as e:
        print(f"❌ Telegram: {e}")


def main():
    setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "test":
        test()
    elif cmd == "chat-id":
        chats = telegram.find_chats()
        if not chats:
            print("Topilmadi. Botni kanalga admin qilib qo'shing, kanalga biror xabar yozing va qayta urinib ko'ring.")
        for cid, title in chats.items():
            print(f"{cid}  {title}")
    elif cmd == "poll":
        conn = connect()
        poll(conn, client())
        for e in conn.execute("SELECT * FROM events WHERE sent=0 ORDER BY ts"):
            print(f"{e['ts']} [{reports.label(e['type'])}] {e['office']} | {e['assignee']} | {e['group_name']}\n   {e['details']}")
    elif cmd in ("digest", "daily", "weekly", "absences"):
        conn, hh = connect(), client()
        {"digest": job_digest, "daily": job_daily, "weekly": job_weekly, "absences": job_absences}[cmd](conn, hh)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
