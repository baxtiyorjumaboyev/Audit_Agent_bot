"""Telegram xabarlari: xatoliklar to'plami, kunlik va haftalik hisobot."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import html

import config
from audit import fmt_date


def escape(text):
    return html.escape(text or "", quote=False)


def group_link(group_id, name):
    """Guruh nomi HolliHop'dagi guruh sahifasiga havola bo'lib chiqadi."""
    name = escape(name)
    if not group_id:
        return name
    return f'<a href="https://{config.HH_DOMAIN}/Learner/Group/{group_id}">{name}</a>'

LABELS = {
    "group_deleted": "🗑 Guruh o'chirildi",
    "group_stopped_early": "⛔ Guruh muddatidan oldin to'xtatildi",
    "schedule_shortened": "✂️ Jadval muddatidan oldin tugatildi",
    "short_plan": "📅 Jadval 1 yildan qisqa tuzilgan",
    "late_join": "🚫 Yangi o'quvchi boshlangan guruhga qo'shildi",
    "teacher_changed": "🔄 Guruh ustozi almashtirildi",
    "absent_no_contact": "📵 Kelmagan o'quvchiga bog'lanilmadi",
    "student_removed": "👋 O'quvchi guruhdan chiqarildi",
}


def daily_control(day, subs, removed):
    """Zamenalar va guruhdan chiqarilgan o'quvchilar ro'yxati."""
    lines = [f"<b>📋 Kunlik nazorat: {fmt_date(day)}</b>", ""]
    lines.append(f"<b>🔁 Zamenalar (asosiy ustoz o'rniga boshqa ustoz): {len(subs)} ta</b>")
    by_office = defaultdict(list)
    for s in subs:
        by_office[s["office"]].append(s)
    for office, items in sorted(by_office.items(), key=lambda x: -len(x[1])):
        lines.append(f"📍 <b>{escape(office)}</b>")
        for s in sorted(items, key=lambda s: s["time"]):
            lines.append(f"   • «{group_link(s['group_id'], s['group'])}» {s['time']}: {escape(s['main'])} o'rniga "
                         f"<b>{escape(s['sub'])}</b> (mas'ul: {escape(s['assignee'])})")
    lines += ["", f"<b>👋 Guruhdan chiqarilgan o'quvchilar: {len(removed)} ta</b>"]
    by_office = defaultdict(list)
    for e in removed:
        by_office[e["office"]].append(e)
    for office, items in sorted(by_office.items(), key=lambda x: -len(x[1])):
        lines.append(f"📍 <b>{escape(office)}</b>")
        for e in items:
            lines.append(f"   • «{group_link(e['group_id'], e['group_name'])}», guruh mas'uli: {escape(e['assignee'])}")
            lines.append(f"     {escape(e['details'])}")
    if removed:
        lines += ["", "⚠️ <i>Har bir chiqarilgan o'quvchi kartasida chiqarish sababi (N.B. izohi) va "
                      "aloqaga chiqish eslatmasi (zadacha) qo'yilganini tekshiring.</i>"]
    return "\n".join(lines)


def label(type_):
    return LABELS.get(type_, type_)


def _when(ts, today):
    t = datetime.fromisoformat(ts)
    return t.strftime("%H:%M") if t.date() == today else t.strftime("%d.%m %H:%M")


def digest(events, title, today):
    if not events:
        return f"<b>{escape(title)}</b>\n\n✅ Yangi xatolik aniqlanmadi."
    by_office = defaultdict(list)
    for e in events:
        by_office[e["office"]].append(e)
    lines = []  # sarlavhasiz: xabar to'g'ridan-to'g'ri umumiy guruhga uzatiladi
    for office, evs in sorted(by_office.items(), key=lambda x: -len(x[1])):
        lines.append(f"📍 <b>{escape(office)}</b>: {len(evs)} ta")
        lines.append("")
        for e in evs:
            lines.append(f"<b>{label(e['type'])}</b>")
            lines.append(f"🕑 {_when(e['ts'], today)} · 👤 {escape(e['assignee'])} (mas'ul)")
            lines.append(f"👥 Guruh: {group_link(e['group_id'], e['group_name'])}")
            lines.append(escape(e["details"] or ""))
            lines.append("")
    return "\n".join(lines).strip()


def absence_alert(rows, title):
    if not rows:
        return f"<b>{escape(title)}</b>\n\n✅ Barcha kelmagan o'quvchilarga bog'lanilgan."
    lines = [f"<b>{escape(title)}</b>", f"Bog'lanilmagan: <b>{len(rows)}</b> ta o'quvchi", ""]
    tree = defaultdict(lambda: defaultdict(list))
    for r in rows:
        tree[r["office"]][r["assignee"]].append(r)
    for office, admins in sorted(tree.items(), key=lambda x: -sum(len(v) for v in x[1].values())):
        lines.append(f"📍 <b>{escape(office)}</b>")
        for admin, items in admins.items():
            lines.append(f"👤 {escape(admin)} (mas'ul): {len(items)} ta")
            for r in sorted(items, key=lambda r: r["lesson_time"]):
                lines.append(f"   • {escape(r['student_name'])} (ID: <code>{r['student_id']}</code>), "
                             f"«{group_link(r['edunit_id'], r['group_name'])}» ({r['lesson_time'] or '—'})")
        lines.append("")
    return "\n".join(lines)


def _type_lines(events):
    return [f"  {label(t)}: {n}" for t, n in Counter(e["type"] for e in events).most_common()]


def _absence_stats(rows):
    stats = defaultdict(lambda: [0, 0])  # filial -> [jami, bog'lanilgan]
    for r in rows:
        stats[r["office"]][0] += 1
        if (r["description"] or "").strip():
            stats[r["office"]][1] += 1
    return stats


def daily(events, absences, day, deadline):
    lines = [f"<b>📊 Kunlik hisobot: {fmt_date(day)}</b>", ""]
    lines.append(f"Bugun aniqlangan xatoliklar: <b>{len(events)}</b> ta")
    if events:
        lines += _type_lines(events)
        lines += ["", "<b>Filiallar bo'yicha:</b>"]
        offices = Counter(e["office"] for e in events)
        for i, (office, n) in enumerate(offices.most_common(), 1):
            admins = Counter(e["assignee"] for e in events if e["office"] == office)
            top = ", ".join(f"{escape(a)}: {c}" for a, c in admins.most_common(3))
            lines.append(f"  {i}. {escape(office)}: {n} ta ({top})")
    total = len(absences)
    done = sum(1 for r in absences if (r["description"] or "").strip())
    lines += ["", f"<b>📵 Bugun kelmagan o'quvchilar: {total} ta</b>"]
    if total:
        lines.append(f"  ✅ Bog'lanib, izoh yozilgan: {done} ({round(100 * done / total)}%)")
        lines.append(f"  ⏳ Hali bog'lanilmagan: {total - done} (muddat {deadline} gacha)")
        stats = _absence_stats(absences)
        worst = sorted(((o, t - d) for o, (t, d) in stats.items() if t - d), key=lambda x: -x[1])[:5]
        if worst:
            lines.append("  Bog'lanilmaganlar ko'p filiallar: " + ", ".join(f"{escape(o)} ({n})" for o, n in worst))
    return "\n".join(lines)


def weekly(events, prev_events, absences, groups_teacher, start, end, subs=(), removed=()):
    lines = [f"<b>📈 HAFTALIK HISOBOT: {fmt_date(start)} – {fmt_date(end)}</b>", ""]
    diff = len(events) - len(prev_events)
    trend = f"▲ +{diff}" if diff > 0 else (f"▼ {diff}" if diff < 0 else "o'zgarmagan")
    lines.append(f"Jami xatoliklar: <b>{len(events)}</b> ta (o'tgan hafta: {len(prev_events)}, {trend})")
    lines += _type_lines(events)

    offices = Counter(e["office"] for e in events)
    if offices:
        lines += ["", "<b>🏢 Filiallar reytingi (eng ko'p xato qilganlar):</b>"]
        for i, (office, n) in enumerate(offices.most_common(10), 1):
            main_type, main_n = Counter(e["type"] for e in events if e["office"] == office).most_common(1)[0]
            lines.append(f"  {i}. {escape(office)}: {n} ta (asosan {label(main_type)} ×{main_n})")

    repeated = Counter((e["assignee"], e["office"], e["type"]) for e in events)
    repeated = [(k, n) for k, n in repeated.most_common() if n >= 3]
    if repeated:
        lines += ["", "<b>🔁 Bir xil xatoni takror qilayotganlar (3 va undan ko'p):</b>"]
        for (admin, office, type_), n in repeated[:10]:
            lines.append(f"  • {escape(admin)} ({escape(office)}): {label(type_)} ×{n}")

    stats = _absence_stats(absences)
    if stats:
        lines += ["", "<b>📵 Kelmaganlarga bog'lanish darajasi:</b>"]
        for office, (t, d) in sorted(stats.items(), key=lambda x: x[1][1] / x[1][0]):
            lines.append(f"  {escape(office)}: {round(100 * d / t)}% ({d}/{t})")

    early = [e for e in events if e["type"] in ("group_stopped_early", "schedule_shortened")]
    if early:
        lines += ["", "<b>⛔ Muddatidan oldin tugatilgan guruhlar:</b>"]
        for e in early:
            lines.append(f"  • «{group_link(e['group_id'], e['group_name'])}» ({escape(e['office'])}, {escape(e['assignee'])})")
            lines += ["    " + escape(l) for l in (e["details"] or "").split("\n") if l.startswith("•")]

    if subs:
        lines += ["", f"<b>🔁 Hafta davomida zamenalar: {len(subs)} ta dars</b>"]
        for office, n in Counter(s["office"] for s in subs).most_common():
            lines.append(f"  {escape(office)}: {n} ta")
        top = [(g, n) for g, n in Counter((s["group"], s["office"]) for s in subs).most_common(5) if n >= 2]
        if top:
            lines.append("  Eng ko'p zamena bo'lgan guruhlar: "
                         + ", ".join(f"«{escape(g)}» ({escape(o)}) ×{n}" for (g, o), n in top))

    if removed:
        lines += ["", f"<b>👋 Guruhdan chiqarilgan o'quvchilar: {len(removed)} ta</b>"]
        for office, n in Counter(e["office"] for e in removed).most_common():
            lines.append(f"  {escape(office)}: {n} ta")

    if groups_teacher:
        lines += ["", "<b>🔄 Ustozi almashtirilgan guruhlar (umumiy son):</b>"]
        for g in groups_teacher[:10]:
            lines.append(f"  • «{group_link(g['id'], g['name'])}» ({escape(g['office'])}): {g['teacher_changes']} marta")

    lines += ["", "<b>🎯 XULOSA VA TAVSIYALAR</b>"]
    if not events:
        lines.append("  ✅ Bu hafta xatolik aniqlanmadi.")
    else:
        top_office, top_n = offices.most_common(1)[0]
        main_type = Counter(e["type"] for e in events if e["office"] == top_office).most_common(1)[0][0]
        lines.append(f"  • Eng ko'p e'tibor kerak bo'lgan filial: <b>{escape(top_office)}</b>, {top_n} ta xato "
                     f"(jamining {round(100 * top_n / len(events))}%). Asosiy muammo: {label(main_type)}.")
        if len(offices) > 1:
            focus = ", ".join(escape(o) for o, _ in offices.most_common(3))
            lines.append(f"  • Nazoratni kuchaytirish kerak bo'lgan filiallar: {focus}.")
        if repeated:
            (admin, office, type_), n = repeated[0]
            lines.append(f"  • {escape(admin)} ({escape(office)}) bir xil xatoni {n} marta qilgan "
                         f"({label(type_)}). U bilan alohida suhbat o'tkazish tavsiya etiladi.")
        if diff > 0:
            lines.append(f"  • Xatoliklar o'tgan haftaga nisbatan {diff} taga ko'paygan.")
        elif diff < 0:
            lines.append(f"  • Xatoliklar o'tgan haftaga nisbatan {-diff} taga kamaygan 👍")
    if stats:
        worst_office, (t, d) = min(stats.items(), key=lambda x: x[1][1] / x[1][0])
        lines.append(f"  • Kelmaganlarga eng kam bog'lanayotgan filial: {escape(worst_office)} "
                     f"({round(100 * d / t)}%).")
    return "\n".join(lines)


def morning(day, events, absences, pending):
    lines = [f"<b>☀️ Ertalabki hisobot: kecha, {fmt_date(day)}</b>", ""]
    lines.append(f"<b>⚠️ Kechagi xatoliklar: {len(events)} ta</b>")
    if events:
        lines += _type_lines(events)
        for office, n in Counter(e["office"] for e in events).most_common(5):
            admins = Counter(e["assignee"] for e in events if e["office"] == office)
            lines.append(f"  📍 {escape(office)}: {n} ta (" + ", ".join(f"{escape(a)}: {c}" for a, c in admins.most_common(3)) + ")")
    total = len(absences)
    done = sum(1 for r in absences if (r["description"] or "").strip())
    lines += ["", f"<b>📵 Kecha kelmaganlar: {total} ta</b>"]
    if total:
        lines.append(f"  ✅ Bog'lanilgan: {done} ({round(100 * done / total)}%) · ❌ Bog'lanilmagan: {total - done}")
        worst = sorted(((o, t - d) for o, (t, d) in _absence_stats(absences).items() if t - d), key=lambda x: -x[1])[:5]
        if worst:
            lines.append("  Eng ko'p bog'lanilmagan: " + ", ".join(f"{escape(o)} ({n})" for o, n in worst))
    lines += ["", f"🌙 Kechqurun va tunda aniqlangan yangi xatoliklar: <b>{pending}</b> ta"
              + (" (ro'yxati keyingi xabarda)" if pending else "")]
    return "\n".join(lines)


def week_range(today):
    """Yakshanbadan shanbagacha bo'lgan joriy hafta."""
    start = today - timedelta(days=(today.weekday() + 1) % 7)
    return start, start + timedelta(days=6)
