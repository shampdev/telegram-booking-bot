"""Выгрузка записей в Excel."""
from collections import defaultdict
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from utils.formatting import parse_dt

HEADER_FILL = PatternFill("solid", fgColor="1F1F1F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TOTAL_FONT = Font(bold=True)
THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(bottom=THIN)

STATUS_FILLS = {
    "Отменена": PatternFill("solid", fgColor="FDE2E1"),
    "Предстоит": PatternFill("solid", fgColor="E3F0FF"),
}


def _status(b: dict, now: datetime) -> str:
    if b["status"] == "cancelled":
        return "Отменена"
    return "Завершена" if parse_dt(b["end_at"]) <= now else "Предстоит"


def _write_table(ws, headers: list[str], rows: list[list], widths: list[int]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 22
    for row in rows:
        ws.append(row)
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"


def build_bookings_xlsx(bookings: list[dict], now: datetime) -> bytes:
    wb = Workbook()

    # Лист 1: все записи
    ws = wb.active
    ws.title = "Записи"
    rows = []
    for b in bookings:
        start = parse_dt(b["start_at"])
        rows.append([
            b["id"], start.date(), start.strftime("%H:%M"), b["service_name"], b["master_name"],
            b["price"], b["client_name"], b["phone"], _status(b, now),
            b["rating"], b["review_comment"] or None,
        ])
    _write_table(
        ws,
        ["№", "Дата", "Время", "Услуга", "Мастер", "Цена, ₽", "Клиент", "Телефон", "Статус", "Оценка", "Отзыв"],
        rows,
        [6, 12, 8, 22, 14, 10, 18, 16, 12, 9, 40],
    )
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 2).number_format = "DD.MM.YYYY"
        ws.cell(r, 6).number_format = "#,##0"
        fill = STATUS_FILLS.get(ws.cell(r, 9).value)
        if fill:
            ws.cell(r, 9).fill = fill
        ws.cell(r, 11).alignment = Alignment(wrap_text=True, vertical="top")
    if rows:
        ws.auto_filter.ref = f"A1:K{ws.max_row}"

    # Лист 2: сводка по мастерам (отменённые записи не учитываются)
    stats = defaultdict(lambda: {"count": 0, "revenue": 0, "ratings": []})
    for b in bookings:
        if b["status"] == "cancelled":
            continue
        s = stats[b["master_name"]]
        s["count"] += 1
        s["revenue"] += b["price"]
        if b["rating"]:
            s["ratings"].append(b["rating"])

    summary = wb.create_sheet("Сводка")
    summary_rows = [
        [name, s["count"], s["revenue"],
         round(sum(s["ratings"]) / len(s["ratings"]), 1) if s["ratings"] else None, len(s["ratings"])]
        for name, s in sorted(stats.items())
    ]
    _write_table(summary, ["Мастер", "Записей", "Выручка, ₽", "Средняя оценка", "Отзывов"],
                 summary_rows, [16, 10, 14, 16, 10])

    if summary_rows:
        last = summary.max_row
        summary.append(["Итого", f"=SUM(B2:B{last})", f"=SUM(C2:C{last})", None, f"=SUM(E2:E{last})"])
        for cell in summary[summary.max_row]:
            cell.font = TOTAL_FONT
            cell.border = Border(top=Side(style="thin", color="000000"))
    for r in range(2, summary.max_row + 1):
        summary.cell(r, 3).number_format = "#,##0"
        summary.cell(r, 4).number_format = "0.0"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
