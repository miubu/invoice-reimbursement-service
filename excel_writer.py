from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from invoice_reader import InvoiceRecord


HEADERS = [
    "实报人",
    "学号",
    "发票上的！发票上的！发票上的！不要瞎写！项目名称",
    "发票上的！发票上的！发票上的！不要瞎写！规格型号",
    "报销类型",
    "票面金额",
    "填报日期",
    "电话",
    "是否传递发票",
]


@dataclass(frozen=True)
class ReimbursementProfile:
    claimant: str = "李府鸿"
    student_id: str = "12604040"
    reimbursement_type: str = "材 料 费"
    phone: str = "13516389370"
    transmit_invoice: str = "YES"


def build_workbook(
    records: list[InvoiceRecord],
    fill_date: datetime,
    profile: ReimbursementProfile,
) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "发票报销填报表"
    sheet.freeze_panes = "A2"
    sheet.sheet_view.showGridLines = False

    sheet.append(HEADERS)
    for record in records:
        sheet.append([
            profile.claimant,
            profile.student_id,
            record.item_name,
            record.specification,
            profile.reimbursement_type,
            float(record.amount),
            fill_date,
            profile.phone,
            profile.transmit_invoice,
        ])

    total_row = len(records) + 2
    sheet.cell(total_row, 5, "票面金额合计")
    sheet.cell(total_row, 6, float(sum(record.amount for record in records)))

    dark_fill = PatternFill("solid", fgColor="1F4E78")
    total_fill = PatternFill("solid", fgColor="D9EAF7")
    white_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    body_font = Font(name="Arial", size=10, color="222222")
    thin_gray = Side(style="thin", color="D9E1F2")

    for cell in sheet[1]:
        cell.fill = dark_fill
        cell.font = white_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(right=Side(style="thin", color="FFFFFF"))
    sheet.row_dimensions[1].height = 48

    for row in sheet.iter_rows(min_row=2, max_row=total_row, min_col=1, max_col=9):
        for cell in row:
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = Border(bottom=thin_gray)

    for row_number in range(2, len(records) + 2):
        sheet.cell(row_number, 2).number_format = "@"
        sheet.cell(row_number, 6).number_format = "0.00"
        sheet.cell(row_number, 7).number_format = "yyyy/m/d"
        sheet.cell(row_number, 8).number_format = "@"
        sheet.cell(row_number, 6).alignment = Alignment(horizontal="right", vertical="top")
        sheet.cell(row_number, 7).alignment = Alignment(horizontal="center", vertical="top")

    for cell in sheet[total_row]:
        cell.fill = total_fill
        cell.font = Font(name="Arial", size=10, bold=True, color="222222")
    sheet.cell(total_row, 6).number_format = "0.00"

    widths = [11, 14, 48, 32, 14, 14, 14, 18, 16]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row_number in range(2, len(records) + 2):
        sheet.row_dimensions[row_number].height = 45

    sheet.auto_filter.ref = f"A1:I{len(records) + 1}"
    return workbook


def build_excel_bytes(
    records: list[InvoiceRecord],
    fill_date: datetime,
    profile: ReimbursementProfile,
) -> bytes:
    workbook = build_workbook(records, fill_date, profile)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def write_excel(
    records: list[InvoiceRecord],
    output_path: Path,
    fill_date: datetime,
    profile: ReimbursementProfile | None = None,
) -> None:
    content = build_excel_bytes(records, fill_date, profile or ReimbursementProfile())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="invoice_reimbursement_",
        suffix=".xlsx",
        dir=output_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        temporary_path.write_bytes(content)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
