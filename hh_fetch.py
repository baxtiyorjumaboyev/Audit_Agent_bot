"""HH tizimlashtirish: HolliHop'dan (faqat o'qish) ma'lumotlarni olib, lokal hh_tizim.db bazaga saqlaydi.
Hech qayerga yuborilmaydi. Ishlatish: python hh_fetch.py"""
import json
import sqlite3
from datetime import date, datetime

import config
from audit import STATUSES, parse_date
from hh_api import HHClient

DB = config.BASE_DIR / "hh_tizim.db"
PERIOD_FROM = date(2026, 1, 1)
EXCLUDE_OFFICES = {"Abutech TEST"}


def connect():
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS raw(key TEXT PRIMARY KEY, json TEXT, fetched_at TEXT)")
    return conn


def save(conn, key, data):
    conn.execute("INSERT OR REPLACE INTO raw VALUES(?, ?, ?)",
                 (key, json.dumps(data, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def load(conn, key):
    row = conn.execute("SELECT json FROM raw WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def group_begin(unit):
    begins = [d for d in (parse_date(i.get("BeginDate")) for i in unit.get("ScheduleItems") or []) if d]
    return min(begins) if begins else None


def main():
    today = date.today()
    client = HHClient(config.HH_DOMAIN, config.HH_API_KEY)
    conn = connect()

    groups = []
    for status in STATUSES:
        for unit in client.groups(config.GROUP_TYPES, status):
            b = group_begin(unit)
            if b and PERIOD_FROM <= b <= today and unit.get("OfficeOrCompanyName") not in EXCLUDE_OFFICES:
                groups.append({**unit, "_status": status})
    save(conn, "groups", groups)
    print(f"Guruhlar: {len(groups)} ta")

    for i, g in enumerate(groups, 1):
        links = client.ed_unit_students(edUnitId=g["Id"], queryDays=True,
                                        dateFrom=group_begin(g).replace(day=1).isoformat(), dateTo=today.isoformat())
        save(conn, f"links:{g['Id']}", links)
        print(f"  {i}/{len(groups)} {g.get('Name')}: {len(links)} ta o'quvchi")

    # O'quvchilar qaysi guruhga ko'chganini aniqlash uchun 2026 yildagi barcha guruh biriktirishlari
    all_links = client.ed_unit_students(edUnitTypes=config.GROUP_TYPES, dateFrom=PERIOD_FROM.isoformat(),
                                        dateTo=date(2027, 12, 31).isoformat())
    save(conn, "all_links", all_links)
    print(f"Barcha biriktirishlar: {len(all_links)} ta")
    save(conn, "fetched", today.isoformat())


if __name__ == "__main__":
    main()
