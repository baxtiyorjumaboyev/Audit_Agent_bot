"""HolliHop Audit Agent.

Buyruqlar:
    python main.py            agentni doimiy ishga tushirish
    python main.py test       HolliHop va Telegram ulanishini tekshirish
    python main.py chat-id    bot qo'shilgan kanal ID'sini topish
    python main.py poll       bir marta tekshiruv o'tkazish
    python main.py digest | daily | weekly | absences   hisobotni hozir yuborish
"""
import json
import logging
import socket
import sys
import time
from datetime import datetime, time as dtime, timedelta

import audit
import config
import reports
import telegram
from db import connect, get_meta, set_meta
from hh_api import HHClient, HHError

log = logging.getLogger("main")


def setup_logging():
    handlers = [logging.FileHandler(config.LOG_PATH, encoding="utf-8")]
    if sys.stdout:  # pythonw (yashirin rejim) da konsol yo'q
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


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
    track_downtime(conn)
    if baseline:
        set_meta(conn, "baseline_done", audit.now().isoformat(timespec="seconds"))
        telegram.send("🤖 <b>Audit agent ishga tushdi.</b>\nHolliHop'ning hozirgi holati saqlandi. "
                      "Bundan keyingi o'zgarishlar nazorat qilinadi.")


def track_downtime(conn):
    """Agent (kompyuter) ishlamagan oraliqlarni ertalabki hisobot uchun eslab qoladi."""
    t = audit.now()
    prev = get_meta(conn, "last_poll_at")
    if prev and t - datetime.fromisoformat(prev) > timedelta(minutes=3 * config.POLL_MINUTES):
        gaps = json.loads(get_meta(conn, "downtime") or "[]")[-20:]
        gaps.append([prev, t.isoformat(timespec="seconds")])
        set_meta(conn, "downtime", json.dumps(gaps))
    set_meta(conn, "last_poll_at", t.isoformat(timespec="seconds"))


def late_note(when, late):
    if not late:
        return ""
    return f"\n\n<i>⏰ Kechikib yuborildi: bu {when.strftime('%d.%m %H:%M')} dagi xabar, kompyuter o'chiq edi.</i>"


# ---------- Rejali ishlar: fn(conn, hh, when, late) ----------

def job_morning(conn, hh, when, late):
    """Kompyuter yoqilgandan keyin birinchi bo'lib: kechagi kun xulosasi va tunda aniqlangan xatoliklar."""
    yesterday = when.date() - timedelta(days=1)
    audit.sync_absences(conn, hh, yesterday)
    events = conn.execute("SELECT * FROM events WHERE substr(ts, 1, 10)=? ORDER BY ts",
                          (yesterday.isoformat(),)).fetchall()
    absences = conn.execute("SELECT * FROM absences WHERE day=? AND pass=1", (yesterday.isoformat(),)).fetchall()
    since = datetime.combine(yesterday, dtime(0, 0))
    gaps = [(datetime.fromisoformat(a), datetime.fromisoformat(b))
            for a, b in json.loads(get_meta(conn, "downtime") or "[]") if datetime.fromisoformat(b) >= since]
    pending = conn.execute("SELECT * FROM events WHERE sent=0 ORDER BY ts").fetchall()
    telegram.send(reports.morning(yesterday, events, absences, gaps, len(pending)))
    if pending:
        telegram.send(reports.digest(pending, "🌙 Kechqurun va tunda aniqlangan xatoliklar", audit.now().date()))
        conn.executemany("UPDATE events SET sent=1 WHERE id=?", [(e["id"],) for e in pending])
        conn.commit()


def job_late_absences(conn, hh, when, late):
    """Kechagi kechki (LATE_LESSON_FROM dan keyingi) darslarda kelmaganlar."""
    day = when.date() - timedelta(days=1)
    audit.sync_absences(conn, hh, day)
    rows = audit.uncontacted_absences(conn, day, late=True)
    if rows:
        telegram.send(reports.absence_alert(
            rows, f"📵 Kechki darsda kelmagan, bog'lanilmagan o'quvchilar ({audit.fmt_date(day)})") + late_note(when, late))
        audit.mark_absences_alerted(conn, rows)


def job_digest(conn, hh, when, late):
    events = conn.execute("SELECT * FROM events WHERE sent=0 ORDER BY ts").fetchall()
    text = reports.digest(events, f"⚠️ Admin xatoliklari: {when.strftime('%d.%m.%Y, %H:%M')}", audit.now().date())
    telegram.send(text + late_note(when, late))
    conn.executemany("UPDATE events SET sent=1 WHERE id=?", [(e["id"],) for e in events])
    conn.commit()


def job_daily(conn, hh, when, late):
    day = when.date()
    if late:
        audit.sync_absences(conn, hh, day)  # kompyuter o'sha kuni o'chiq bo'lgan bo'lishi mumkin
    events = conn.execute("SELECT * FROM events WHERE substr(ts, 1, 10)=? ORDER BY ts", (day.isoformat(),)).fetchall()
    absences = conn.execute("SELECT * FROM absences WHERE day=? AND pass=1", (day.isoformat(),)).fetchall()
    telegram.send(reports.daily(events, absences, day, config.ABSENCE_DEADLINE) + late_note(when, late))


def job_absences(conn, hh, when, late):
    day = when.date()
    audit.sync_absences(conn, hh, day)
    rows = audit.uncontacted_absences(conn, day, late=False)
    telegram.send(reports.absence_alert(
        rows, f"📵 Kelmagan o'quvchiga bog'lanilmadi: {audit.fmt_date(day)}, {config.ABSENCE_DEADLINE} holatiga")
        + late_note(when, late))
    audit.mark_absences_alerted(conn, rows)


def job_weekly(conn, hh, when, late):
    start, end = reports.week_range(when.date())
    prev = start - timedelta(days=7)

    def events_between(a, b):
        return conn.execute("SELECT * FROM events WHERE substr(ts, 1, 10) BETWEEN ? AND ? ORDER BY ts",
                            (a.isoformat(), b.isoformat())).fetchall()

    absences = conn.execute("SELECT * FROM absences WHERE day BETWEEN ? AND ? AND pass=1",
                            (start.isoformat(), end.isoformat())).fetchall()
    teacher_groups = conn.execute("SELECT * FROM groups WHERE teacher_changes > 0 AND present=1 "
                                  "ORDER BY teacher_changes DESC").fetchall()
    telegram.send(reports.weekly(events_between(start, end), events_between(prev, start - timedelta(days=1)),
                                 absences, teacher_groups, start, end) + late_note(when, late))


# (nomi, vaqti, hafta kuni yoki None, funksiya). Bir vaqtga to'g'ri kelsa, ro'yxatdagi tartibda bajariladi.
JOBS = ([("morning", config.MORNING_TIME, None, job_morning),
         ("late_absences", config.DIGEST_TIMES[0], None, job_late_absences)]
        + [(f"digest_{t}", t, None, job_digest) for t in config.DIGEST_TIMES]
        + [("daily", config.DAILY_REPORT_TIME, None, job_daily),
           ("absences", config.ABSENCE_DEADLINE, None, job_absences),
           ("weekly", config.WEEKLY_REPORT_TIME, config.WEEKLY_REPORT_DAY, job_weekly)])


def mark_done(conn, name, day):
    conn.execute("INSERT OR IGNORE INTO jobs VALUES(?, ?)", (name, day.isoformat()))
    conn.commit()


def pending_jobs(conn, t):
    """Vaqti o'tgan, lekin bajarilmagan ishlar, jumladan kompyuter o'chiq bo'lgan paytdagilari ham."""
    started = datetime.fromisoformat(get_meta(conn, "baseline_done"))
    due = []
    for order, (name, hhmm, weekday, fn) in enumerate(JOBS):
        lookback = 7 if weekday is not None else config.CATCHUP_DAYS
        for back in range(lookback, -1, -1):
            day = t.date() - timedelta(days=back)
            if weekday is not None and day.weekday() != weekday:
                continue
            h, m = map(int, hhmm.split(":"))
            when = datetime.combine(day, dtime(h, m))
            if started <= when <= t and not conn.execute(
                    "SELECT 1 FROM jobs WHERE name=? AND day=?", (name, day.isoformat())).fetchone():
                due.append((when, order, name, fn))
    due.sort(key=lambda j: (j[0], j[1]))
    # Xatoliklar ro'yxati va ertalabki hisobot har safar eng yangi holatni oladi, shuning uchun oxirgisi yetarli
    for fn in (job_digest, job_morning):
        same = [j for j in due if j[3] is fn]
        for j in same[:-1]:
            mark_done(conn, j[2], j[0].date())
            due.remove(j)
    return due


def single_instance():
    """Agent ikki marta ishga tushib, xabarlar takrorlanmasligi uchun."""
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47831))
    except OSError:
        log.info("Agent allaqachon ishlayapti.")
        print("Agent allaqachon ishlayapti, bu oynani yopishingiz mumkin.")
        sys.exit(0)
    return lock


def run():
    lock = single_instance()  # noqa: F841 (jarayon oxirigacha ushlab turiladi)
    conn, hh = connect(), client()
    last_poll, failures = None, 0
    log.info("Audit agent ishga tushdi. Tekshiruv har %d daqiqada.", config.POLL_MINUTES)
    while True:
        t = audit.now()
        due = pending_jobs(conn, t) if get_meta(conn, "baseline_done") else []
        if last_poll is None or t - last_poll >= timedelta(minutes=config.POLL_MINUTES) or (
                due and t - last_poll >= timedelta(minutes=2)):
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
            if not due and get_meta(conn, "baseline_done"):
                due = pending_jobs(conn, audit.now())
        for when, _, name, fn in due:
            late = audit.now() - when > timedelta(minutes=30)
            try:
                fn(conn, hh, when, late)
                mark_done(conn, name, when.date())
                log.info("%s bajarildi (%s)%s", name, when, " — kechikib" if late else "")
            except Exception:
                log.exception("%s bajarilmadi, keyinroq qayta urinadi", name)
                break  # internet hali ulanmagan bo'lishi mumkin; tartib buzilmasligi uchun to'xtaymiz
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
    elif cmd in ("digest", "daily", "weekly", "absences", "morning"):
        conn, hh = connect(), client()
        fn = {"digest": job_digest, "daily": job_daily, "weekly": job_weekly, "absences": job_absences,
              "morning": job_morning}[cmd]
        fn(conn, hh, audit.now(), False)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
