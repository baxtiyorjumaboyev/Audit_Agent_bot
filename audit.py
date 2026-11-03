"""HolliHop holatini oldingisi bilan solishtirib, xatoliklarni aniqlaydi."""
import json
import logging
from datetime import date, datetime, timedelta

import config
from hh_api import HHError

log = logging.getLogger("audit")

STATUSES = ["Reserve", "Forming", "Working", "Stopped", "Finished"]
ENDED = {"Stopped", "Finished"}
STATUS_UZ = {
    "Reserve": "Zaxira", "Forming": "Shakllanmoqda", "Working": "O'qimoqda",
    "Stopped": "To'xtatilgan", "Finished": "Tugatilgan",
}


def now():
    return datetime.now(config.TZ).replace(tzinfo=None)


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def fmt_date(d):
    return d.strftime("%d.%m.%Y") if d else "—"


def months(days):
    return round(days / 30.4, 1)


def add_event(conn, type_, office, assignee, group_id, group_name, details, sent=0):
    conn.execute(
        "INSERT INTO events(ts, type, office, assignee, group_id, group_name, details, sent)"
        " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (now().isoformat(timespec="seconds"), type_, office, assignee, group_id, group_name, details, sent))
    log.info("XATOLIK [%s] %s | %s | %s", type_, office, assignee, group_name)


def event_for_group(conn, type_, g, details):
    add_event(conn, type_, g["office"], g["assignee"], g["id"], g["name"], details)


# ---------- Guruhlar ----------

def current_teacher_ids(items, today):
    active = [i for i in items
              if (parse_date(i.get("BeginDate")) or date.min) <= today <= (parse_date(i.get("EndDate")) or date.max)]
    if not active and items:
        active = [max(items, key=lambda i: parse_date(i.get("BeginDate")) or date.min)]
    return sorted({t for i in active for t in (i.get("TeacherIds") or [])})


def group_info(unit, status, today):
    items = unit.get("ScheduleItems") or []
    begins = [d for d in (parse_date(i.get("BeginDate")) for i in items) if d]
    ends = [d for d in (parse_date(i.get("EndDate")) for i in items) if d]
    names = {}
    for item in items:
        for tid, tname in zip(item.get("TeacherIds") or [], item.get("Teachers") or []):
            names[str(tid)] = tname
    return {
        "id": unit["Id"],
        "name": unit.get("Name") or f"Guruh #{unit['Id']}",
        "office": unit.get("OfficeOrCompanyName") or "Filial ko'rsatilmagan",
        "assignee": (unit.get("Assignee") or {}).get("FullName") or "Mas'ul ko'rsatilmagan",
        "status": status,
        "begin": min(begins).isoformat() if begins else None,
        "end": max(ends).isoformat() if ends else None,
        "teacher_ids": json.dumps(sorted({t for i in items for t in (i.get("TeacherIds") or [])})),
        "current_teachers": json.dumps(current_teacher_ids(items, today)),
        "teacher_names": json.dumps(names, ensure_ascii=False),
        "schedule": json.dumps(items, ensure_ascii=False),
        "students_count": unit.get("StudentsCount"),
        "description": (unit.get("Description") or "").strip(),
    }


def teacher_list(ids, names):
    return ", ".join(names.get(str(i), f"#{i}") for i in sorted(ids)) or "—"


def explain_early_end(client, g):
    """Guruh nega erta tugatilganini HolliHop ma'lumotlaridan taxmin qiladi."""
    try:
        links = client.ed_unit_students(edUnitId=g["id"])
    except HHError as e:
        return f"• Tahlil qilib bo'lmadi: {e}"
    students = {l["StudentClientId"]: l for l in links if l.get("StudentClientId")}
    moved, targets = 0, set()
    for sid in list(students)[:40]:
        try:
            other = client.ed_unit_students(studentClientId=sid, statuses="Normal")
        except HHError:
            continue
        names = {o.get("EdUnitName") or "" for o in other
                 if o.get("EdUnitId") != g["id"] and o.get("EdUnitType") in config.GROUP_TYPES}
        if names:
            moved += 1
            targets |= names
    total = len(students)
    reasons = []
    if total == 0:
        reasons.append("Guruhga birorta ham o'quvchi biriktirilmagan, guruh xato ochilgan bo'lishi mumkin.")
    else:
        if moved:
            where = ", ".join(sorted(t for t in targets if t)[:3])
            reasons.append(f"{moved}/{total} o'quvchi hozir boshqa guruhda o'qiyapti ({where}). "
                           "Guruhlar birlashtirilgan yoki o'quvchilar ko'chirilgan bo'lishi mumkin.")
        if total - moved > 0:
            reasons.append(f"{total - moved}/{total} o'quvchi boshqa guruhga o'tmagan, o'qishni to'xtatgan bo'lishi mumkin.")
    if g["description"]:
        reasons.append(f"Guruh izohi: «{g['description'][:200]}»")
    else:
        reasons.append("Guruh izohiga sabab yozilmagan, buni filial adminidan so'rash kerak.")
    return "\n".join("• " + r for r in reasons)


def check_group_changes(conn, client, old, g, today):
    begin, end = parse_date(g["begin"]), parse_date(g["end"])
    if old is None:
        if begin and end and (end - begin).days < config.COURSE_MIN_DAYS:
            event_for_group(conn, "short_plan", g,
                            f"Jadval: {fmt_date(begin)} – {fmt_date(end)} ({months((end - begin).days)} oy). "
                            "Kurs jadvali 1 yilga tuzilishi kerak.")
        return

    ended_early = False
    if g["status"] in ENDED and old["status"] not in ENDED and begin and (today - begin).days < config.COURSE_MIN_DAYS:
        ended_early = True
        event_for_group(conn, "group_stopped_early", g,
                        f"Holat: {STATUS_UZ.get(old['status'], old['status'])} → {STATUS_UZ.get(g['status'], g['status'])}. "
                        f"Guruh {fmt_date(begin)} da boshlangan, faqat {months((today - begin).days)} oy o'qigan.\n"
                        f"Sabab tahlili:\n{explain_early_end(client, g)}")

    old_end = parse_date(old["end"])
    if (not ended_early and begin and end and (old_end is None or end < old_end)
            and (end - begin).days < config.COURSE_MIN_DAYS):
        if old_end is None:
            event_for_group(conn, "short_plan", g,
                            f"Jadval: {fmt_date(begin)} – {fmt_date(end)} ({months((end - begin).days)} oy). "
                            "Kurs jadvali 1 yilga tuzilishi kerak.")
        else:
            event_for_group(conn, "schedule_shortened", g,
                            f"Jadval tugash sanasi {fmt_date(old_end)} → {fmt_date(end)} ga o'zgartirildi. "
                            f"Jami {months((end - begin).days)} oy, 1 yil bo'lishi kerak edi.\n"
                            f"Sabab tahlili:\n{explain_early_end(client, g)}")

    check_teacher_changes(conn, old, g, today)


def main_teacher(items):
    """Guruhda eng ko'p dars o'tgan (o'tadigan) ustoz, ya'ni asosiy ustoz."""
    weight = {}
    for item in items:
        b = parse_date(item.get("BeginDate"))
        if not b:
            continue
        e = min(parse_date(item.get("EndDate")) or b + timedelta(days=400), b + timedelta(days=400))
        lessons = count_lessons([item], b, e + timedelta(days=1))
        for tid in item.get("TeacherIds") or []:
            weight[tid] = weight.get(tid, 0) + lessons
    return max(weight, key=weight.get) if weight else None


def check_teacher_changes(conn, old, g, today):
    """Jadvalga yangi qo'shilgan (yoki ustozi o'zgartirilgan) qatorlarni asosiy ustoz bilan solishtiradi.
    Zamena tugab, asosiy ustoz qaytishi xato hisoblanmaydi, chunki bunda yangi qator paydo bo'lmaydi."""
    old_items = {i.get("Id"): i for i in json.loads(old["schedule"] or "[]")}
    main = main_teacher(list(old_items.values()))
    if main is None:
        return
    names = {**json.loads(old["teacher_names"] or "{}"), **json.loads(g["teacher_names"])}
    main_name = names.get(str(main), f"#{main}")
    lines = []
    for item in json.loads(g["schedule"]):
        tids = set(item.get("TeacherIds") or [])
        prev = old_items.get(item.get("Id"))
        if not tids or tids == {main} or (prev is not None and set(prev.get("TeacherIds") or []) == tids):
            continue
        b, e = parse_date(item.get("BeginDate")), parse_date(item.get("EndDate"))
        if e and e < today - timedelta(days=7):
            continue  # eski o'tib ketgan jadvalni tahrirlash
        who = teacher_list(tids, names)
        if b and e and (e - b).days <= 14:
            when = fmt_date(b) if b == e else f"{fmt_date(b)} – {fmt_date(e)}"
            lines.append(f"Zamena: {when} kuni darsni asosiy ustoz {main_name} o'rniga {who} o'tadi.")
        else:
            lines.append(f"Asosiy ustoz almashtirildi: {main_name} → {who} ({fmt_date(b)} dan).")
    if lines:
        g["teacher_changes"] = (old["teacher_changes"] or 0) + 1
        event_for_group(conn, "teacher_changed", g,
                        "\n".join(lines) + f"\nAgent kuzatgan davrda bu guruhda {g['teacher_changes']}-marta.")


def sync_groups(conn, client, baseline):
    today = now().date()
    seen = {}
    for status in STATUSES:
        for unit in client.groups(config.GROUP_TYPES, status):
            seen[unit["Id"]] = group_info(unit, status, today)

    old = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM groups")}
    present_before = [gid for gid, r in old.items() if r["present"]]
    if present_before and len(seen) < 0.8 * len(present_before):
        log.warning("API %d ta guruh qaytardi (oldin %d ta). Xato bo'lishi mumkin, bu tekshiruv o'tkazib yuborildi.",
                    len(seen), len(present_before))
        return

    stamp = now().isoformat(timespec="seconds")
    for gid, g in seen.items():
        o = old.get(gid)
        g["teacher_changes"] = (o or {}).get("teacher_changes") or 0
        if not baseline:
            check_group_changes(conn, client, o, g, today)
        g["updated_at"] = stamp
        conn.execute(
            """INSERT INTO groups(id, name, office, assignee, status, begin, end, teacher_ids, current_teachers,
                   teacher_names, schedule, students_count, description, teacher_changes, present, updated_at)
               VALUES(:id, :name, :office, :assignee, :status, :begin, :end, :teacher_ids, :current_teachers,
                   :teacher_names, :schedule, :students_count, :description, :teacher_changes, 1, :updated_at)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, office=excluded.office, assignee=excluded.assignee,
                   status=excluded.status, begin=excluded.begin, end=excluded.end, teacher_ids=excluded.teacher_ids,
                   current_teachers=excluded.current_teachers, teacher_names=excluded.teacher_names,
                   schedule=excluded.schedule, students_count=excluded.students_count,
                   description=excluded.description, teacher_changes=excluded.teacher_changes, present=1,
                   updated_at=excluded.updated_at""", g)

    for gid in present_before:
        if gid in seen:
            continue
        try:
            if client.group_by_id(gid, config.GROUP_TYPES):
                continue
        except HHError as e:
            log.warning("Guruh #%s tekshirilmadi: %s", gid, e)
            continue
        o = old[gid]
        conn.execute("UPDATE groups SET present=0, updated_at=? WHERE id=?", (stamp, gid))
        if not baseline:
            names = json.loads(o["teacher_names"] or "{}")
            event_for_group(conn, "group_deleted", o,
                            f"Holati: {STATUS_UZ.get(o['status'], o['status'])}, o'quvchilar: {o['students_count'] or 0} ta, "
                            f"ustoz: {teacher_list(json.loads(o['current_teachers'] or '[]'), names)}, "
                            f"jadval: {fmt_date(parse_date(o['begin']))} – {fmt_date(parse_date(o['end']))}.")
    conn.commit()
    log.info("Guruhlar tekshirildi: %d ta", len(seen))


# ---------- Yangi o'quvchi boshlangan guruhga ----------

def count_lessons(items, start, until):
    """start (kiritilgan) dan until (kiritilmagan) gacha o'tilgan darslar soni."""
    days = set()
    for item in items:
        mask = item.get("Weekdays") or 0
        d = max(parse_date(item.get("BeginDate")) or start, start)
        last = min(parse_date(item.get("EndDate")) or until, until - timedelta(days=1))
        while d <= last:
            if mask & (1 << d.weekday()):
                days.add(d)
            d += timedelta(days=1)
    return len(days)


def check_new_link(conn, client, link, g):
    if not g or not g["begin"]:
        return
    start = parse_date(link.get("BeginDate"))
    if not start:
        return
    held = count_lessons(json.loads(g["schedule"] or "[]"), parse_date(g["begin"]), start)
    if held <= config.MAX_LESSONS_FOR_NEW_STUDENT:
        return
    try:
        history = client.ed_unit_students(studentClientId=link["StudentClientId"])
    except HHError:
        history = []
    transferred = any(h.get("EdUnitId") != g["id"] and h.get("EdUnitType") in config.GROUP_TYPES
                      and (parse_date(h.get("BeginDate")) or date.max) <= start for h in history)
    if transferred:
        return  # boshqa guruhdan ko'chirilgan bo'lsa, bu xato emas
    event_for_group(conn, "late_join", g,
                    f"O'quvchi: {link.get('StudentName') or '—'} (ID: {link['StudentClientId']}). "
                    f"Guruh {fmt_date(parse_date(g['begin']))} da boshlangan, o'quvchi {fmt_date(start)} dan qo'shildi. "
                    f"Bu vaqtgacha {held} ta dars o'tilgan (ruxsat: {config.MAX_LESSONS_FOR_NEW_STUDENT} tagacha). "
                    "O'quvchi oldin boshqa guruhda o'qimagan, ya'ni yangi o'quvchi.")


def sync_links(conn, client, baseline):
    today = now().date()
    links = client.ed_unit_students(edUnitTypes=config.GROUP_TYPES,
                                    dateFrom=(today - timedelta(days=30)).isoformat(),
                                    dateTo=(today + timedelta(days=60)).isoformat())
    known = {(r[0], r[1]) for r in conn.execute("SELECT edunit_id, student_id FROM links")}
    groups = {r["id"]: r for r in conn.execute("SELECT * FROM groups")}
    stamp = now().isoformat(timespec="seconds")
    for link in links:
        key = (link.get("EdUnitId"), link.get("StudentClientId"))
        if None in key or key in known:
            continue
        known.add(key)
        conn.execute("INSERT OR IGNORE INTO links VALUES(?, ?, ?, ?, ?, ?, ?)",
                     (key[0], key[1], link.get("StudentName"), link.get("BeginDate"), link.get("EndDate"),
                      link.get("Status"), stamp))
        if not baseline:
            check_new_link(conn, client, link, groups.get(key[0]))
    conn.commit()
    log.info("O'quvchi biriktirishlari tekshirildi: %d ta", len(links))


# ---------- Kelmagan o'quvchilar ----------

def sync_absences(conn, client, day):
    links = client.ed_unit_students(edUnitTypes=config.GROUP_TYPES, dateFrom=day.isoformat(),
                                    dateTo=day.isoformat(), queryDays=True)
    groups = {r["id"]: r for r in conn.execute("SELECT id, name, assignee FROM groups")}
    stamp = now().isoformat(timespec="seconds")
    count = 0
    for link in links:
        eid, sid = link.get("EdUnitId"), link.get("StudentClientId")
        for d in link.get("Days") or []:
            if parse_date(d.get("Date")) != day:
                continue
            description = (d.get("Description") or "").strip()
            if not d.get("Pass"):
                conn.execute("UPDATE absences SET pass=0 WHERE edunit_id=? AND student_id=? AND day=?",
                             (eid, sid, day.isoformat()))
                continue
            count += 1
            g = groups.get(eid)
            conn.execute(
                """INSERT INTO absences(edunit_id, student_id, day, student_name, group_name, office, assignee,
                       lesson_time, pass, description, first_seen)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(edunit_id, student_id, day) DO UPDATE SET pass=1, description=excluded.description,
                       lesson_time=excluded.lesson_time, assignee=excluded.assignee, office=excluded.office""",
                (eid, sid, day.isoformat(), link.get("StudentName") or str(sid),
                 link.get("EdUnitName") or (g["name"] if g else ""), link.get("EdUnitOfficeOrCompanyName") or "",
                 g["assignee"] if g else "Mas'ul ko'rsatilmagan", (link.get("BeginTime") or "")[:5],
                 description, stamp))
    conn.commit()
    log.info("%s: kelmaganlar %d ta", day, count)


def uncontacted_absences(conn, day, late):
    rows = conn.execute(
        "SELECT * FROM absences WHERE day=? AND pass=1 AND alerted=0 AND TRIM(COALESCE(description, ''))=''",
        (day.isoformat(),)).fetchall()
    return [r for r in rows if (r["lesson_time"] >= config.LATE_LESSON_FROM) == late]


def mark_absences_alerted(conn, rows):
    for r in rows:
        add_event(conn, "absent_no_contact", r["office"], r["assignee"], r["edunit_id"], r["group_name"],
                  f"O'quvchi: {r['student_name']} (ID: {r['student_id']}), dars: {fmt_date(parse_date(r['day']))} {r['lesson_time']}. "
                  "Bog'lanib, sababi izohga yozilmagan.", sent=1)
        conn.execute("UPDATE absences SET alerted=1 WHERE edunit_id=? AND student_id=? AND day=?",
                     (r["edunit_id"], r["student_id"], r["day"]))
    conn.commit()
