"""HH tizimlashtirish: hh_tizim.db dagi ma'lumotdan "HH tizimlashtirish.xlsx" faylini yaratadi.
Avval: python hh_fetch.py  (HolliHop'dan faqat o'qiydi). Keyin: python hh_tizim.py"""
import math
from collections import Counter, defaultdict
from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config
from audit import STATUS_UZ, main_teacher, parse_date
from hh_fetch import connect, load

OUT = config.BASE_DIR / "HH tizimlashtirish.xlsx"
MONTHS_UZ = ["Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr",
             "Dekabr"]
CLOSE_DAYS = 3        # guruh yopilishiga shuncha kun qolganda chiqqanlar "guruh yopildi" hisoblanadi
MOVE_BEFORE, MOVE_AFTER = 21, 45  # chiqqandan keyin shu oraliqda boshqa guruhga yozilsa, "ko'chdi"

# Moliya varag'idagi kiritiladigan qiymatlar (formulalar shu kataklarga havola qiladi)
PRICE, OTHER = "Moliya!$C$4", "Moliya!$C$5"
T1, T2, S1, S2, S3 = "Moliya!$C$9", "Moliya!$C$10", "Moliya!$D$9", "Moliya!$D$10", "Moliya!$D$11"
DEFAULT_PRICE = 600_000


def month_key(d):
    return f"{d.year}-{d.month:02d} {MONTHS_UZ[d.month - 1]}"


def salary_formula(cell):
    return f"IF({cell}<={T1},{S1},IF({cell}<={T2},{S2},{S3}))"


def link_url(gid):
    return f"https://{config.HH_DOMAIN}/Learner/Group/{gid}"


# ---------- Tahlil ----------

def student_periods(links, today):
    """Har bir o'quvchi uchun guruhdagi davr: (boshlanish, haqiqiy tugash, dars kunlari)."""
    out = {}
    for l in links:
        sid = l.get("StudentClientId")
        b, e = parse_date(l.get("BeginDate")), parse_date(l.get("EndDate"))
        if not sid or not b:
            continue
        days = {parse_date(d.get("Date")): not d.get("Pass") for d in l.get("Days") or [] if parse_date(d.get("Date"))}
        days = {d: v for d, v in days.items() if d <= today}
        if l.get("Status") == "Stopped":
            # To'xtatilgan biriktirishda tugash sanasi ba'zan yangilanmaydi: oxirgi dars kuni olinadi
            last = max(days) if days else b
            e = min(e or last, last)
        e = e or date.max
        p = out.setdefault(sid, {"id": sid, "name": l.get("StudentName") or "", "begin": b, "end": e, "days": {}})
        p["begin"], p["end"] = min(p["begin"], b), max(p["end"], e)
        p["days"].update(days)
    return out


def analyze():
    conn = connect()
    today = parse_date(load(conn, "fetched"))
    groups = load(conn, "groups")
    by_student = defaultdict(list)
    for l in load(conn, "all_links"):
        if l.get("StudentClientId") and parse_date(l.get("BeginDate")):
            by_student[l["StudentClientId"]].append(l)

    rows, leavers, monthly = [], [], []
    for g in groups:
        items = g.get("ScheduleItems") or []
        begin = min(parse_date(i["BeginDate"]) for i in items if parse_date(i.get("BeginDate")))
        names = {tid: n for i in items for tid, n in zip(i.get("TeacherIds") or [], i.get("Teachers") or [])}
        mt = main_teacher(items)
        studs = student_periods(load(conn, f"links:{g['Id']}") or [], today)
        lessons = sorted({d for s in studs.values() for d in s["days"] if d >= begin})
        ended = g["_status"] in ("Stopped", "Finished")
        closed_on = lessons[-1] if ended and lessons else None
        L1 = lessons[0] if lessons else None
        L3 = lessons[2] if len(lessons) >= 3 else None

        def inside(s, d):
            return s["begin"] <= d <= s["end"]

        r = {
            "id": g["Id"], "name": g.get("Name") or f"#{g['Id']}", "office": g.get("OfficeOrCompanyName") or "—",
            "month": month_key(begin), "begin": begin, "status": STATUS_UZ.get(g["_status"], g["_status"]),
            "teacher": names.get(mt, "—") if mt else "—", "discipline": " ".join((g.get("Discipline") or "").split()),
            "L1": L1, "L3": L3, "lessons": len(lessons),
            "n1": sum(inside(s, L1) for s in studs.values()) if L1 else 0,
            "came1": sum(bool(s["days"].get(L1)) for s in studs.values()) if L1 else 0,
            "n3": sum(inside(s, L3) for s in studs.values()) if L3 else None,
            "join13": sum(L1 < s["begin"] <= L3 for s in studs.values()) if L3 else None,
            "left13": sum(L1 <= s["end"] < L3 for s in studs.values()) if L3 else None,
            "join_after": sum(s["begin"] > (L3 or L1) for s in studs.values()) if L1 else 0,
            "now": 0 if ended else sum(inside(s, today) for s in studs.values()),
            "moved": 0, "quit": 0, "closed": 0,
        }

        # Chiqib ketganlar: qaysi guruhga ko'chdi yoki butunlay ketdi
        group_leavers = []
        for s in studs.values():
            if not L1 or s["end"] >= today or s["end"] < L1 or s["begin"] > s["end"]:
                continue
            if closed_on and s["end"] >= closed_on - timedelta(days=CLOSE_DAYS):
                kind, target = "closed", None
            else:
                cands = [o for o in by_student[s["id"]] if o.get("EdUnitId") != g["Id"]
                         and s["end"] - timedelta(days=MOVE_BEFORE) <= parse_date(o["BeginDate"])
                         <= s["end"] + timedelta(days=MOVE_AFTER)]
                target = min(cands, key=lambda o: abs((parse_date(o["BeginDate"]) - s["end"]).days)) if cands else None
                kind = "moved" if target else "quit"
            r[kind] += 1
            group_leavers.append((s, kind, target))
            leavers.append({"group": r, "student": s, "kind": kind, "target": target,
                            "attended": sum(s["days"].values())})
        rows.append(r)

        # Oy kesimida harakat
        if L1:
            m = date(L1.year, L1.month, 1)
            while m <= today:
                nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
                last = min(nxt - timedelta(days=1), today)
                start = max(m, L1)
                monthly.append({
                    "group": r, "month": month_key(m),
                    "start": sum(inside(s, start) for s in studs.values()),
                    "joined": sum(start < s["begin"] <= last for s in studs.values()),
                    **{k: sum(1 for s, kind, _ in group_leavers if kind == k and m <= s["end"] <= last)
                       for k in ("moved", "quit", "closed")},
                    "end": 0 if (closed_on and closed_on <= last) else sum(inside(s, last) for s in studs.values()),
                })
                m = nxt
    rows.sort(key=lambda r: (r["office"], r["begin"], r["name"]))
    return today, rows, leavers, monthly


# ---------- Excel ----------

HEAD = PatternFill("solid", fgColor="1F4E78")
SUB = PatternFill("solid", fgColor="DDEBF7")
INPUT = PatternFill("solid", fgColor="FFF2CC")
TOTAL = PatternFill("solid", fgColor="F2F2F2")
GOOD, BAD = PatternFill("solid", fgColor="C6EFCE"), PatternFill("solid", fgColor="FFC7CE")
WHITE_B, BOLD = Font(bold=True, color="FFFFFF"), Font(bold=True)
LINK = Font(color="0563C1", underline="single")
THIN = Border(*(Side(style="thin", color="BFBFBF"),) * 4)
SUM_FMT, PCT = '#,##0', '0%'


def header(ws, row, titles, widths=None):
    for c, t in enumerate(titles, 1):
        cell = ws.cell(row, c, t)
        cell.fill, cell.font, cell.border = HEAD, WHITE_B, THIN
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[row].height = 45
    for c, w in enumerate(widths or [], 1):
        ws.column_dimensions[get_column_letter(c)].width = w


def put_link(cell, name, gid):
    cell.value, cell.hyperlink, cell.font = name, link_url(gid), LINK


def fmt_d(d):
    return d.strftime("%d.%m.%Y") if d else "—"


def sheet_groups(wb, rows, today):
    ws = wb.active
    ws.title = "Guruhlar"
    titles = ["№", "Filial", "Ochilgan oy", "Guruh (HH havola)", "Holat", "Asosiy ustoz", "Yo'nalish",
              "1-dars sanasi", "1-darsda o'quvchi", "1-darsga kelgan", "3-dars sanasi", "3-darsda o'quvchi (1–3 dars)",
              "1–3 darsda qo'shilgan", "1–3 darsda chiqqan", "3-darsdan keyin qo'shilgan", "Jami chiqqan",
              "shundan boshqa guruhga ko'chgan", "shundan butunlay ketgan", "guruh yopilishi bilan",
              f"Hozir ({fmt_d(today)})", "Saqlanish (hozir / 1-dars)", "O'tilgan darslar",
              "Ustoz oyligi (1-dars soni bo'yicha)", "Ochilishdagi oylik daromad", "Hozirgi oylik daromad",
              "Hozirgi oylik foyda", "Hozirgi marja"]
    header(ws, 1, titles, [5, 12, 16, 34, 13, 22, 22, 11, 9, 9, 11, 10, 9, 9, 9, 8, 9, 9, 9, 9, 10, 8, 13, 13, 13,
                           13, 9])
    for i, r in enumerate(rows, 2):
        vals = [i - 1, r["office"], r["month"], None, r["status"], r["teacher"], r["discipline"], fmt_d(r["L1"]),
                r["n1"], r["came1"], fmt_d(r["L3"]), r["n3"], r["join13"], r["left13"], r["join_after"],
                f"=Q{i}+R{i}+S{i}", r["moved"], r["quit"], r["closed"], r["now"],
                f'=IF(I{i}=0,"",T{i}/I{i})', r["lessons"],
                f"=IF(I{i}=0,0,{salary_formula(f'I{i}')})", f"=I{i}*{PRICE}", f"=T{i}*{PRICE}",
                f"=IF(T{i}=0,0,Y{i}-W{i}-{OTHER})", f'=IF(Y{i}=0,"",Z{i}/Y{i})']
        for c, v in enumerate(vals, 1):
            cell = ws.cell(i, c, v)
            cell.border = THIN
        put_link(ws.cell(i, 4), r["name"], r["id"])
        for c in (23, 24, 25, 26):
            ws.cell(i, c).number_format = SUM_FMT
        ws.cell(i, 21).number_format = ws.cell(i, 27).number_format = PCT
    n = len(rows) + 1
    t = n + 1
    ws.cell(t, 4, "JAMI / O'RTACHA").font = BOLD
    for c in list(range(9, 21)) + [22, 23, 24, 25, 26]:
        col = get_column_letter(c)
        ws.cell(t, c, f"=SUM({col}2:{col}{n})").number_format = SUM_FMT
    ws.cell(t, 21, f"=T{t}/I{t}").number_format = PCT
    ws.cell(t, 27, f"=IF(Y{t}=0,\"\",Z{t}/Y{t})").number_format = PCT
    for c in range(1, 28):
        ws.cell(t, c).fill, ws.cell(t, c).font = TOTAL, BOLD
    ws.conditional_formatting.add(f"Z2:Z{n}", CellIsRule(operator="lessThan", formula=["0"], fill=BAD))
    ws.conditional_formatting.add(f"U2:U{n}", CellIsRule(operator="lessThan", formula=["0.6"], fill=BAD))
    ws.freeze_panes = "E2"
    ws.auto_filter.ref = f"A1:AA{n}"


def sheet_branch_month(wb, rows):
    ws = wb.create_sheet("Filial × Oy")
    titles = ["Guruh (HH havola)", "Holat", "Ustoz", "1-dars sanasi", "1-darsda", "3-darsda (1–3 dars)",
              "Qo'shilgan (1-darsdan keyin)", "Ko'chgan", "Butunlay ketgan", "Hozir", "Saqlanish"]
    header(ws, 1, titles, [36, 13, 22, 11, 9, 10, 11, 9, 9, 9, 10])
    row = 2
    blocks = defaultdict(list)
    for r in rows:
        blocks[(r["office"], r["month"])].append(r)
    for office in sorted({k[0] for k in blocks}):
        ws.cell(row, 1, f"{office.upper()}").font = Font(bold=True, size=13, color="1F4E78")
        row += 1
        for (o, month) in sorted(k for k in blocks if k[0] == office):
            grp = blocks[(o, month)]
            c = ws.cell(row, 1, f"{office} — {month.split(' ', 1)[1]} {month[:4]} ({len(grp)} ta guruh)")
            for cc in range(1, 12):
                ws.cell(row, cc).fill = SUB
            c.font = BOLD
            row += 1
            first = row
            for r in grp:
                put_link(ws.cell(row, 1), r["name"], r["id"])
                vals = [r["status"], r["teacher"], fmt_d(r["L1"]), r["n1"], r["n3"],
                        (r["join13"] or 0) + r["join_after"], r["moved"], r["quit"], r["now"],
                        f'=IF(E{row}=0,"",J{row}/E{row})']
                for cc, v in enumerate(vals, 2):
                    ws.cell(row, cc, v)
                ws.cell(row, 11).number_format = PCT
                for cc in range(1, 12):
                    ws.cell(row, cc).border = THIN
                row += 1
            ws.cell(row, 1, "Jami").font = BOLD
            for cc in range(5, 11):
                col = get_column_letter(cc)
                ws.cell(row, cc, f"=SUM({col}{first}:{col}{row - 1})").font = BOLD
            ws.cell(row, 11, f'=IF(E{row}=0,"",J{row}/E{row})').number_format = PCT
            for cc in range(1, 12):
                ws.cell(row, cc).fill = TOTAL
            row += 2
    ws.freeze_panes = "A2"


def sheet_matrix(wb, rows):
    ws = wb.create_sheet("Filial jadvali")
    offices = sorted({r["office"] for r in rows})
    months = sorted({r["month"] for r in rows})
    ws.column_dimensions["A"].width = 22
    row = 1
    tables = [("Ochilgan guruhlar soni", "COUNTIFS(Guruhlar!$B:$B,$A{r},Guruhlar!$C:$C,{m})", "0"),
              ("1-darsdagi o'quvchilar (jami)", "SUMIFS(Guruhlar!$I:$I,Guruhlar!$B:$B,$A{r},Guruhlar!$C:$C,{m})", "0"),
              ("3-darsdagi o'quvchilar (jami)", "SUMIFS(Guruhlar!$L:$L,Guruhlar!$B:$B,$A{r},Guruhlar!$C:$C,{m})", "0"),
              ("Hozirgi o'quvchilar (jami)", "SUMIFS(Guruhlar!$T:$T,Guruhlar!$B:$B,$A{r},Guruhlar!$C:$C,{m})", "0"),
              ("Hozirgi oylik foyda (so'm)", "SUMIFS(Guruhlar!$Z:$Z,Guruhlar!$B:$B,$A{r},Guruhlar!$C:$C,{m})",
               SUM_FMT)]
    for title, f, fmt in tables:
        ws.cell(row, 1, title).font = Font(bold=True, size=12, color="1F4E78")
        row += 1
        header(ws, row, ["Filial"] + [m.split(" ", 1)[1] for m in months] + ["Jami"])
        hdr = row
        for c, m in enumerate(months, 2):
            ws.cell(hdr + len(offices) + 2, c, m).font = Font(color="FFFFFF", size=1)  # yashirin oy kaliti
        for i, office in enumerate(offices, 1):
            r = hdr + i
            ws.cell(r, 1, office).border = THIN
            for c in range(2, len(months) + 2):
                key = f"{get_column_letter(c)}${hdr + len(offices) + 2}"
                cell = ws.cell(r, c, "=" + f.format(r=r, m=key))
                cell.number_format, cell.border = fmt, THIN
            last = get_column_letter(len(months) + 1)
            ws.cell(r, len(months) + 2, f"=SUM(B{r}:{last}{r})").font = BOLD
            ws.cell(r, len(months) + 2).number_format = fmt
        tr = hdr + len(offices) + 1
        ws.cell(tr, 1, "Jami").font = BOLD
        for c in range(2, len(months) + 3):
            col = get_column_letter(c)
            cell = ws.cell(tr, c, f"=SUM({col}{hdr + 1}:{col}{tr - 1})")
            cell.font, cell.fill, cell.number_format = BOLD, TOTAL, fmt
        row = tr + 3
    for c in range(2, len(months) + 3):
        ws.column_dimensions[get_column_letter(c)].width = 12


def sheet_monthly(wb, monthly):
    ws = wb.create_sheet("Oylik harakat")
    header(ws, 1, ["Filial", "Guruh (HH havola)", "Oy", "Oy boshida", "Qo'shildi", "Boshqa guruhga ko'chdi",
                   "Butunlay ketdi", "Guruh yopildi", "Oy oxirida"], [12, 36, 16, 9, 9, 10, 9, 9, 9])
    for i, m in enumerate(monthly, 2):
        ws.cell(i, 1, m["group"]["office"])
        put_link(ws.cell(i, 2), m["group"]["name"], m["group"]["id"])
        for c, v in enumerate([m["month"], m["start"], m["joined"], m["moved"], m["quit"], m["closed"], m["end"]], 3):
            ws.cell(i, c, v)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:I{len(monthly) + 1}"


def sheet_leavers(wb, leavers):
    ws = wb.create_sheet("Chiqib ketganlar")
    header(ws, 1, ["Filial", "Guruh (HH havola)", "O'quvchi ID", "O'quvchi", "Guruhga kirgan", "Chiqqan sana",
                   "Kelgan darslari", "Natija", "Qaysi guruhga o'tdi", "O'sha guruh filiali", "O'tgan sana"],
           [12, 34, 10, 30, 11, 11, 9, 22, 34, 14, 11])
    kinds = {"moved": "Boshqa guruhga ko'chdi", "quit": "Butunlay chiqib ketdi", "closed": "Guruh yopildi"}
    leavers.sort(key=lambda x: (x["group"]["office"], x["group"]["name"], x["student"]["end"]))
    for i, x in enumerate(leavers, 2):
        s, t = x["student"], x["target"]
        ws.cell(i, 1, x["group"]["office"])
        put_link(ws.cell(i, 2), x["group"]["name"], x["group"]["id"])
        for c, v in enumerate([s["id"], s["name"], fmt_d(s["begin"]), fmt_d(s["end"]), x["attended"],
                               kinds[x["kind"]]], 3):
            ws.cell(i, c, v)
        ws.cell(i, 8).fill = {"moved": SUB, "quit": BAD, "closed": TOTAL}[x["kind"]]
        if t:
            put_link(ws.cell(i, 9), t.get("EdUnitName") or f"#{t['EdUnitId']}", t["EdUnitId"])
            ws.cell(i, 10, t.get("EdUnitOfficeOrCompanyName") or "")
            ws.cell(i, 11, fmt_d(parse_date(t.get("BeginDate"))))
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:K{len(leavers) + 1}"


BUCKETS = [("1–8 o'quvchi", 1, 8, 7), ("9 o'quvchi", 9, 9, 9), ("10–12 o'quvchi", 10, 12, 11),
           ("13–14 o'quvchi", 13, 14, 14), ("15–17 o'quvchi", 15, 17, 16), ("18+ o'quvchi", 18, 999, 19)]


def sheet_finance(wb, rows, today):
    ws = wb.create_sheet("Moliya")
    end = len(rows) + 1  # Guruhlar varag'idagi oxirgi guruh qatori (JAMI qatori kirmaydi)

    def rng(col):
        return f"Guruhlar!${col}$2:${col}${end}"

    for col, w in zip("ABCDEFGHIJ", [4, 44, 16, 16, 16, 16, 16, 16, 16, 16]):
        ws.column_dimensions[col].width = w
    ws["B1"] = "MOLIYA MODELI — sariq kataklarni o'zgartirsangiz, butun fayl qayta hisoblanadi"
    ws["B1"].font = Font(bold=True, size=13, color="1F4E78")
    ws["B3"], ws["B3"].font = "Kiritiladigan qiymatlar", BOLD
    inputs = [(4, "O'quvchining oylik to'lovi (o'rtacha, chegirmalar bilan)", DEFAULT_PRICE),
              (5, "Guruhga boshqa oylik xarajat (ijara, admin, marketing...)", 0),
              (6, "Hisobot sanasi", fmt_d(today))]
    for r, label, v in inputs:
        ws.cell(r, 2, label)
        c = ws.cell(r, 3, v)
        c.number_format, c.border = SUM_FMT, THIN
        if r < 6:
            c.fill = INPUT
    ws["D4"] = "2026-yil oktabr: to'lagan mijoz boshiga o'rtacha ~598 000 so'm (HH to'lovlari)"
    ws["D5"] = "Hozircha 0 — haqiqiy xarajat kiritilsa, foyda aniqroq chiqadi"
    for c in ("D4", "D5"):
        ws[c].font = Font(italic=True, color="808080")

    ws["B8"], ws["B8"].font = "Ustoz oyligi tariflari (1-darsdagi o'quvchi soniga qarab)", BOLD
    ws["C8"], ws["D8"] = "… gacha (o'quvchi)", "Oylik (so'm)"
    for r, (lim, sal) in zip((9, 10, 11), ((9, 800_000), (14, 1_200_000), (999, 1_600_000))):
        ws.cell(r, 2, ["1-tarif: 9 tagacha", "2-tarif: 10–14", "3-tarif: 15 va undan ko'p"][r - 9])
        for c, v in ((3, lim), (4, sal)):
            cell = ws.cell(r, c, v)
            cell.fill, cell.border, cell.number_format = INPUT, THIN, SUM_FMT

    # 1) Har bir guruh hajmi uchun iqtisod
    r0 = 13
    ws.cell(r0, 2, "1. Guruh hajmi bo'yicha oylik iqtisod (1 ta guruh)").font = Font(bold=True, size=12)
    hdr = ["", "O'quvchi soni", "Daromad", "Ustoz oyligi", "Boshqa xarajat", "Foyda", "Marja",
           "Har o'quvchidan foyda", "Qo'shimcha o'quvchi foydasi"]
    for c, t in enumerate(hdr[1:], 2):
        cell = ws.cell(r0 + 1, c, t)
        cell.fill, cell.font, cell.alignment = HEAD, WHITE_B, Alignment(wrap_text=True, horizontal="center")
    for n in range(1, 26):
        r = r0 + 1 + n
        ws.cell(r, 2, n)
        ws.cell(r, 3, f"=B{r}*{PRICE}")
        ws.cell(r, 4, f"={salary_formula(f'B{r}')}")
        ws.cell(r, 5, f"={OTHER}")
        ws.cell(r, 6, f"=C{r}-D{r}-E{r}")
        ws.cell(r, 7, f"=IF(C{r}=0,\"\",F{r}/C{r})")
        ws.cell(r, 8, f"=F{r}/B{r}")
        ws.cell(r, 9, "" if n == 1 else f"=F{r}-F{r - 1}")
        for c in range(2, 10):
            ws.cell(r, c).border = THIN
            ws.cell(r, c).number_format = PCT if c == 7 else SUM_FMT
    last = r0 + 26
    ws.conditional_formatting.add(f"F{r0 + 2}:F{last}", CellIsRule(operator="lessThan", formula=["0"], fill=BAD))
    ws.conditional_formatting.add(f"I{r0 + 3}:I{last}", CellIsRule(operator="lessThan", formula=["0"], fill=BAD))
    br = last + 2
    ws.cell(br, 2, "Zararsizlik nuqtasi (1-tarifda), o'quvchi").font = BOLD
    ws.cell(br, 3, f"=ROUNDUP(({S1}+{OTHER})/{PRICE},0)").font = BOLD
    ws.cell(br + 1, 2, "Zararsizlik nuqtasi (2-tarifda), o'quvchi")
    ws.cell(br + 1, 3, f"=ROUNDUP(({S2}+{OTHER})/{PRICE},0)")
    ws.cell(br + 2, 2, "Zararsizlik nuqtasi (3-tarifda), o'quvchi")
    ws.cell(br + 2, 3, f"=ROUNDUP(({S3}+{OTHER})/{PRICE},0)")

    # 2) 2026 yil faktik taqsimot
    r1 = br + 5
    ws.cell(r1, 2, "2. 2026-yil faktik: guruhlar 1-darsda nechta o'quvchi bilan ochilgan").font = Font(bold=True,
                                                                                                         size=12)
    hdr2 = ["Hajm guruhi", "Guruhlar soni", "Ulushi", "O'rtacha 1-darsda", "O'rtacha hozir", "Saqlanish",
            "Hozirgi oylik foyda (jami)", "Foyda / guruh"]
    for c, t in enumerate(hdr2, 2):
        cell = ws.cell(r1 + 1, c, t)
        cell.fill, cell.font, cell.alignment = HEAD, WHITE_B, Alignment(wrap_text=True, horizontal="center")
    started = rng("I")
    for k, (label, lo, hi, _) in enumerate(BUCKETS):
        r = r1 + 2 + k
        cond = f'{started},">={lo}",{started},"<={hi}"'
        ws.cell(r, 2, label)
        ws.cell(r, 3, f"=COUNTIFS({cond})")
        ws.cell(r, 4, f"=C{r}/SUM($C${r1 + 2}:$C${r1 + 1 + len(BUCKETS)})")
        ws.cell(r, 5, f'=IFERROR(AVERAGEIFS({rng("I")},{cond}),"")')
        ws.cell(r, 6, f'=IFERROR(AVERAGEIFS({rng("T")},{cond},{rng("T")},">0"),"")')
        ws.cell(r, 7, f'=IFERROR(SUMIFS({rng("T")},{cond})/SUMIFS({rng("I")},{cond}),"")')
        ws.cell(r, 8, f"=SUMIFS({rng("Z")},{cond})")
        ws.cell(r, 9, f'=IFERROR(H{r}/COUNTIFS({cond},{rng("T")},">0"),"")')
        for c in range(2, 10):
            ws.cell(r, c).border = THIN
            ws.cell(r, c).number_format = {4: PCT, 5: "0.0", 6: "0.0", 7: PCT}.get(c, SUM_FMT)
    ws.cell(r1 + 2 + len(BUCKETS), 2, "Izoh: Saqlanish = hozir qolgan / 1-darsda bo'lgan (yopilgan guruhlar ham "
                                      "hisobda). Foyda — faqat ishlab turgan guruhlar.").font = Font(italic=True,
                                                                                                     color="808080")

    # 3) Ssenariy: N ta guruh ochilsa
    r2 = r1 + 5 + len(BUCKETS)
    ws.cell(r2, 2, "3. Ssenariy: Noyabrda ochiladigan guruhlar").font = Font(bold=True, size=12)
    ws.cell(r2 + 1, 2, "Ochiladigan guruhlar soni")
    ws.cell(r2 + 1, 3, 10).fill = INPUT
    ws.cell(r2 + 2, 2, "3 oydan keyingi saqlanish (faktik o'rtacha, o'zgartirsa bo'ladi)")
    ws.cell(r2 + 2, 3, round(retention(rows, today)[0], 2)).fill = INPUT
    ws.cell(r2 + 2, 3).number_format = PCT
    N, KEEP = f"$C${r2 + 1}", f"$C${r2 + 2}"
    hdr3 = ["Hajm guruhi", "Ulushi (%)", "Guruhlar soni", "1 guruhdagi o'quvchi", "Oylik daromad", "Ustoz oyligi",
            "Oylik foyda (1-oy)", "Oylik foyda (3 oydan keyin)"]
    for c, t in enumerate(hdr3, 2):
        cell = ws.cell(r2 + 4, c, t)
        cell.fill, cell.font, cell.alignment = HEAD, WHITE_B, Alignment(wrap_text=True, horizontal="center")
    first = r2 + 5
    for k, (label, lo, hi, rep) in enumerate(BUCKETS):
        r = first + k
        ws.cell(r, 2, label)
        ws.cell(r, 3, f"=D{r1 + 2 + k}").fill = INPUT     # standart: 2026 faktik ulush
        ws.cell(r, 4, f"={N}*C{r}")
        ws.cell(r, 5, rep).fill = INPUT
        ws.cell(r, 6, f"=D{r}*E{r}*{PRICE}")
        ws.cell(r, 7, f"=D{r}*{salary_formula(f'E{r}')}")
        ws.cell(r, 8, f"=F{r}-G{r}-D{r}*{OTHER}")
        # 3 oydan keyin o'quvchi kamayadi, ustoz oyligi esa ochilish tarifida qoladi
        ws.cell(r, 9, f"=D{r}*(E{r}*{KEEP}*{PRICE}-{salary_formula(f'E{r}')}-{OTHER})")
        for c in range(2, 10):
            ws.cell(r, c).border = THIN
            ws.cell(r, c).number_format = {3: PCT, 4: "0.0", 5: "0"}.get(c, SUM_FMT)
    tr = first + len(BUCKETS)
    ws.cell(tr, 2, "Jami").font = BOLD
    for c in (3, 4, 6, 7, 8, 9):
        col = get_column_letter(c)
        cell = ws.cell(tr, c, f"=SUM({col}{first}:{col}{tr - 1})")
        cell.font, cell.fill, cell.number_format = BOLD, TOTAL, PCT if c == 3 else SUM_FMT
    ws.cell(tr + 1, 2, "Marja (1-oy)")
    ws.cell(tr + 1, 8, f"=IF(F{tr}=0,\"\",H{tr}/F{tr})").number_format = PCT
    ws.cell(tr + 2, 2, "Ulushlar jami 100% bo'lishi kerak").font = Font(italic=True, color="808080")

    # 4) Tayyor ssenariylar taqqoslash
    r3 = tr + 5
    ws.cell(r3, 2, "4. Taqqoslash: N ta guruh hammasi bir xil hajmda ochilsa (1-oy / 3 oydan keyin)").font = Font(
        bold=True, size=12)
    for c, t in enumerate(["Har guruhda", "Jami o'quvchi", "Oylik foyda (1-oy)", "Marja", "Foyda (3 oydan keyin)",
                           "Har o'quvchidan foyda"], 2):
        cell = ws.cell(r3 + 1, c, t)
        cell.fill, cell.font, cell.alignment = HEAD, WHITE_B, Alignment(wrap_text=True, horizontal="center")
    for k, n in enumerate([6, 8, 9, 10, 12, 14, 15, 16, 18, 20]):
        r = r3 + 2 + k
        sal = salary_formula(f"B{r}")
        ws.cell(r, 2, n)
        ws.cell(r, 3, f"={N}*B{r}")
        ws.cell(r, 4, f"={N}*(B{r}*{PRICE}-{sal}-{OTHER})")
        ws.cell(r, 5, f"=D{r}/({N}*B{r}*{PRICE})")
        ws.cell(r, 6, f"={N}*(B{r}*{KEEP}*{PRICE}-{sal}-{OTHER})")
        ws.cell(r, 7, f"=D{r}/C{r}")
        for c in range(2, 8):
            ws.cell(r, c).border = THIN
            ws.cell(r, c).number_format = PCT if c == 5 else SUM_FMT
    return r3


def retention(rows, today):
    """Kamida 3 oy o'qiyotgan (yopilmagan) guruhlarda: hozirgi o'quvchi / 1-darsdagi o'quvchi."""
    w = [r for r in rows if r["L1"] and (today - r["L1"]).days >= 90 and r["status"] == STATUS_UZ["Working"]]
    return sum(r["now"] for r in w) / max(1, sum(r["n1"] for r in w)), len(w)


def money(v):
    return f"{v:,}".replace(",", " ")


def summary_text(rows, leavers, today):
    """Xulosa varag'i uchun faktik raqamlar (standart narx bilan)."""
    started = [r for r in rows if r["L1"]]
    n1 = [r["n1"] for r in started]
    tiers = Counter("9 tagacha" if n <= 9 else "10–14" if n <= 14 else "15+" for n in n1)
    kinds = Counter(x["kind"] for x in leavers)
    keep, n_keep = retention(rows, today)
    l3 = [r for r in started if r["n3"] is not None]
    drop13 = sum(r["left13"] for r in l3) / max(1, sum(r["n1"] for r in l3))
    quits = [x for x in leavers if x["kind"] == "quit"]
    q_month = Counter(min((x["student"]["end"] - max(x["student"]["begin"], x["group"]["L1"])).days // 30, 3)
                      for x in quits)
    closed_groups = sum(1 for r in started if r["status"] in (STATUS_UZ["Stopped"], STATUS_UZ["Finished"]))

    def sal(n):
        return 800_000 if n <= 9 else 1_200_000 if n <= 14 else 1_600_000

    p = DEFAULT_PRICE
    margin = {n: (n * p - sal(n)) / (n * p) for n in (9, 10, 14, 15)}
    by_office = defaultdict(lambda: [0, 0, 0])
    for r in started:
        by_office[r["office"]][0] += 1
        by_office[r["office"]][1] += r["n1"]
        by_office[r["office"]][2] += r["now"]
    office_avg = sorted(((o, v[1] / v[0], v[0]) for o, v in by_office.items()), key=lambda x: -x[1])
    small = sum(1 for n in n1 if n < 9)
    lines = [
        ("Umumiy", [
            f"{fmt_d(date(2026, 1, 1))} – {fmt_d(today)}: {len(rows)} ta guruh ochilgan, shundan {len(started)} tasida "
            f"darslar boshlangan.",
            f"Guruhlar o'rtacha {sum(n1) / max(1, len(n1)):.1f} ta o'quvchi bilan ochilgan (mediana "
            f"{sorted(n1)[len(n1) // 2] if n1 else 0}).",
            f"Ochilish hajmi: 9 tagacha — {tiers['9 tagacha']} ta, 10–14 — {tiers['10–14']} ta, 15+ — {tiers['15+']} ta "
            f"guruh. 9 tadan kam o'quvchi bilan ochilgan: {small} ta.",
            f"Hozir jami {sum(r['now'] for r in rows)} ta o'quvchi o'qiyapti (1-darsda {sum(n1)} ta edi).",
        ]),
        ("O'quvchi harakati", [
            f"1–3 darslar orasida o'quvchilarning atigi {drop13:.0%} qismi chiqib ketgan — ochilish bosqichi barqaror.",
            f"Asosiy yo'qotish keyin: butunlay ketganlarning {q_month[0]} tasi 1-oyda, {q_month[1]} tasi 2-oyda, "
            f"{q_month[2]} tasi 3-oyda, {q_month[3]} tasi undan keyin chiqqan. Ya'ni 1–2-oylar eng xavfli davr "
            f"({(q_month[0] + q_month[1]) / max(1, len(quits)):.0%}).",
            f"Kamida 3 oydan beri o'qiyotgan {n_keep} ta guruhda 1-darsdagi o'quvchilarga nisbatan {keep:.0%} "
            "o'quvchi bor (saqlanish koeffitsienti; keyin qo'shilganlar ham hisobda).",
            f"{closed_groups} ta guruh to'xtatilgan yoki tugatilgan.",
            f"Chiqib ketganlar: {kinds['moved']} ta boshqa guruhga ko'chgan, {kinds['quit']} ta butunlay ketgan, "
            f"{kinds['closed']} ta guruh yopilishi bilan chiqqan.",
        ]),
        ("Filiallar (o'rtacha ochilish hajmi)", [
            f"{o}: {avg:.1f} o'quvchi/guruh ({cnt} ta guruh)" for o, avg, cnt in office_avg]),
        (f"Moliya (o'quvchi to'lovi {money(p)} so'm deb olinganda, boshqa xarajatlarsiz)", [
            f"Faqat ustoz oyligini hisoblasak, zararsizlik nuqtasi: 1-tarifda {math.ceil(800_000 / p)} ta, 2-tarifda "
            f"{math.ceil(1_200_000 / p)} ta, 3-tarifda {math.ceil(1_600_000 / p)} ta o'quvchi. Ya'ni ustoz oyligi "
            f"o'zi zarar keltirmaydi — haqiqiy zararsizlik nuqtasini ijara, admin va marketing xarajatlari belgilaydi.",
            f"Marja: 9 o'quvchi — {margin[9]:.0%}, 10 o'quvchi — {margin[10]:.0%}, 14 o'quvchi — {margin[14]:.0%}, "
            f"15 o'quvchi — {margin[15]:.0%}.",
            "Tarif chegaralarida (10 va 15-o'quvchi) ustoz oyligi 400 000 so'mga oshadi, shuning uchun 10- va "
            f"15-o'quvchi faqat {money(p - 400_000)} so'm qo'shimcha foyda beradi, qolgan har bir o'quvchi esa "
            f"{money(p)} so'm.",
            "Marja eng yuqori bo'ladigan nuqtalar — tarif chegarasidan oldingi hajmlar: 9 va 14 ta (15+ da esa 18–20 "
            "ta). 10–13 ta o'quvchida marja 9 talikdan past, lekin har qo'shilgan o'quvchi baribir foydani oshiradi "
            "(10 ta: +200 000, 11–14: har biri +600 000 so'm). Demak 9 dan oshsa, 14 gacha yig'ish kerak.",
            f"Saqlanish {keep:.0%} bo'lgani uchun 9 ta bilan ochilgan guruh 3 oydan keyin ~{9 * keep:.1f} o'quvchiga, "
            f"14 talik ~{14 * keep:.1f} ga tushadi. Guruhni kattaroq ochish keyingi chiqib ketishlarga zaxira beradi.",
        ]),
        ("Tavsiyalar", [
            "1. Guruhni kamida 9, iloji bo'lsa 14 ta o'quvchi bilan oching. 10–11 ta bo'lsa, 1–2 hafta ichida "
            "14 gacha to'ldirish marjani 9 talik darajasidan yuqoriga chiqaradi.",
            "2. 1–2-oylarda o'quvchini ushlab qolish (oylik natija ko'rsatish, ota-ona bilan aloqa, kelmaganlarga "
            "qo'ng'iroq) to'g'ridan-to'g'ri foydani oshiradi: har saqlangan o'quvchi oyiga to'liq to'lov keltiradi.",
            "3. 'Chiqib ketganlar' varag'ida 'Butunlay chiqib ketdi' qatorlarini filial bo'yicha tekshiring — "
            "bular yo'qotilgan daromad.",
            "4. 'Moliya' varag'iga haqiqiy o'rtacha to'lov va guruh boshiga boshqa xarajatni kiriting — barcha "
            "foyda/marja hisoblari avtomatik yangilanadi.",
        ]),
    ]
    return lines


def sheet_summary(wb, rows, leavers, today):
    ws = wb.create_sheet("Xulosa", 0)
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 130
    ws["B1"] = f"HH tizimlashtirish — 2026-yil yanvar – {fmt_d(today)}"
    ws["B1"].font = Font(bold=True, size=15, color="1F4E78")
    ws["B2"] = ("Manba: HolliHop (faqat o'qish). Varaqlar: Guruhlar, Filial × Oy, Filial jadvali, Oylik harakat, "
                "Chiqib ketganlar, Moliya. Guruh nomini bossangiz HH'da ochiladi.")
    ws["B2"].font = Font(italic=True, color="808080")
    r = 4
    for title, items in summary_text(rows, leavers, today):
        ws.cell(r, 2, title).font = Font(bold=True, size=12, color="1F4E78")
        r += 1
        for line in items:
            c = ws.cell(r, 2, "• " + line if not line[:2].rstrip(".").isdigit() else line)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            r += 1
        r += 1
    ws.cell(r, 2, "Hisoblash qoidalari").font = Font(bold=True, size=12, color="1F4E78")
    for line in [
        "1-darsda o'quvchi: guruhning birinchi o'tilgan darsi kuni ro'yxatda bo'lganlar. 'Kelgan' — davomatda bor.",
        "3-darsda o'quvchi: 3-dars kuni ro'yxatda bo'lganlar (1–3 darslarda qo'shilgan va chiqqanlar hisobga olingan).",
        f"Ko'chdi: o'quvchi chiqqan sanadan {MOVE_BEFORE} kun oldin – {MOVE_AFTER} kun keyin oralig'ida boshqa guruhga "
        "yozilgan. Aks holda — butunlay chiqib ketgan.",
        f"Guruh yopildi: guruh To'xtatilgan/Tugatilgan, o'quvchi oxirgi darsga {CLOSE_DAYS} kun qolganda yoki undan "
        "keyin chiqqan.",
        "Ustoz oyligi tarifi 1-darsdagi o'quvchi soni bo'yicha olinadi (9 tagacha / 10–14 / 15+). '14+' 15 va undan "
        "ko'p deb talqin qilindi; Moliya varag'ida o'zgartirsa bo'ladi.",
        f"Daromad = o'quvchi soni × o'rtacha oylik to'lov (standart {money(DEFAULT_PRICE)} so'm, 2026-yil oktabr HH "
        "to'lovlari bo'yicha mijoz boshiga o'rtacha).",
    ]:
        r += 1
        ws.cell(r, 2, "• " + line).alignment = Alignment(wrap_text=True)


def main():
    today, rows, leavers, monthly = analyze()
    wb = Workbook()
    sheet_groups(wb, rows, today)
    sheet_branch_month(wb, rows)
    sheet_matrix(wb, rows)
    sheet_monthly(wb, monthly)
    sheet_leavers(wb, leavers)
    sheet_finance(wb, rows, today)
    sheet_summary(wb, rows, leavers, today)
    wb.save(OUT)
    print(f"Tayyor: {OUT}  ({len(rows)} guruh, {len(leavers)} chiqqan o'quvchi)")


if __name__ == "__main__":
    main()
