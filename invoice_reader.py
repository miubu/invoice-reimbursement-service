from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber


MONEY_RE = re.compile(r"[（(]小写[）)]\s*[¥￥]?\s*([0-9][0-9,]*\.\d{2})")
INVOICE_NO_RE = re.compile(r"发票号码[：:]\s*(\d+)")
INVOICE_DATE_RE = re.compile(r"开票日期[：:]\s*(\d{4}年\d{2}月\d{2}日)")
NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?%?$")


@dataclass
class InvoiceRecord:
    pdf_name: str
    invoice_number: str
    invoice_date: str
    item_name: str
    specification: str
    amount: Decimal | None
    needs_review: bool
    review_reasons: list[str]

    def to_json_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["amount"] = f"{self.amount:.2f}" if self.amount is not None else None
        data["status"] = "待人工核对" if self.needs_review else "已识别"
        return data


def _find_word(words: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    return next((word for word in words if word["text"] == text), None)


def _cluster_rows(words: list[dict[str, Any]], tolerance: float = 2.5) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda value: (value["top"], value["x0"])):
        if not rows or abs(word["top"] - rows[-1][0]["top"]) > tolerance:
            rows.append([word])
        else:
            rows[-1].append(word)
    for row in rows:
        row.sort(key=lambda value: value["x0"])
    return rows


def _column_text(rows: list[list[dict[str, Any]]], left: float, right: float) -> str:
    """同一票面行保留词间空格，跨行按发票单元格连续拼接。"""
    lines: list[str] = []
    for row in rows:
        pieces = [word["text"] for word in row if left <= word["x0"] < right]
        if pieces:
            lines.append(" ".join(pieces).strip())
    return "".join(lines).strip()


def _extract_detail_cells(page: Any) -> tuple[str, str, list[str]]:
    words = page.extract_words(
        x_tolerance=1,
        y_tolerance=1,
        keep_blank_chars=False,
        use_text_flow=False,
    )
    item_header = _find_word(words, "项目名称")
    spec_header = _find_word(words, "规格型号")
    if not item_header or not spec_header:
        return "", "", ["未定位到项目名称/规格型号表头"]

    header_top = min(item_header["top"], spec_header["top"])
    unit_candidates = [
        word for word in words
        if word["x0"] > spec_header["x0"]
        and abs(word["top"] - header_top) <= 4
        and word["text"] in {"单", "单位"}
    ]
    if not unit_candidates:
        return "", "", ["未定位到规格型号右侧的单位列"]

    spec_left = spec_header["x0"] - 3
    unit_left = min(word["x0"] for word in unit_candidates) - 3
    total_candidates = [
        word for word in words
        if word["top"] > header_top + 8
        and word["x0"] < 130
        and word["text"] in {"合", "合计"}
    ]
    if not total_candidates:
        return "", "", ["未定位到明细表合计行"]
    total_top = min(word["top"] for word in total_candidates)

    data_words = [word for word in words if header_top + 5 < word["top"] < total_top - 1]
    rows = _cluster_rows(data_words)
    start_indexes: list[int] = []
    for index, row in enumerate(rows):
        has_item = any(word["x0"] < spec_left for word in row)
        has_right_number = any(
            word["x0"] >= unit_left and NUMBER_RE.fullmatch(word["text"])
            for word in row
        )
        if has_item and has_right_number:
            start_indexes.append(index)

    if not start_indexes:
        return "", "", ["未识别到发票项目明细行"]

    detail_blocks: list[tuple[str, str, bool]] = []
    for position, start in enumerate(start_indexes):
        end = start_indexes[position + 1] if position + 1 < len(start_indexes) else len(rows)
        block_rows = rows[start:end]
        item = _column_text(block_rows, 0, spec_left)
        specification = _column_text(block_rows, spec_left, unit_left)
        is_discount = any(
            word["x0"] >= unit_left and word["text"].startswith("-")
            for word in rows[start]
        )
        detail_blocks.append((item, specification, is_discount))

    positive_blocks = [(item, spec) for item, spec, discount in detail_blocks if not discount]
    if not positive_blocks:
        positive_blocks = [(item, spec) for item, spec, _ in detail_blocks]

    distinct: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item, spec in positive_blocks:
        pair = (item, spec)
        if pair not in seen:
            seen.add(pair)
            distinct.append(pair)

    reasons: list[str] = []
    if len(distinct) != 1:
        reasons.append(f"识别到 {len(distinct)} 个不同的非折扣项目，无法安全合并为一行")
    item = "；".join(pair[0] for pair in distinct)
    specification = "；".join(pair[1] for pair in distinct)
    if not item:
        reasons.append("项目名称为空")
    return item, specification, reasons


def read_invoice(pdf_path: Path) -> InvoiceRecord:
    reasons: list[str] = []
    item_name = ""
    specification = ""
    amount: Decimal | None = None
    invoice_number = ""
    invoice_date = ""

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if len(pdf.pages) != 1:
                reasons.append(f"PDF 共 {len(pdf.pages)} 页，需要人工确认发票页")
            page = pdf.pages[0]
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            if len(text.strip()) < 80:
                reasons.append("PDF 文字层过少或不可读")

            amount_matches = MONEY_RE.findall(text)
            if len(amount_matches) != 1:
                reasons.append(f"价税合计（小写）匹配到 {len(amount_matches)} 处")
            else:
                try:
                    amount = Decimal(amount_matches[0].replace(",", ""))
                except InvalidOperation:
                    reasons.append("价税合计（小写）不是有效金额")

            number_match = INVOICE_NO_RE.search(text)
            date_match = INVOICE_DATE_RE.search(text)
            invoice_number = number_match.group(1) if number_match else ""
            invoice_date = date_match.group(1) if date_match else ""
            if not invoice_number:
                reasons.append("未识别到发票号码")

            item_name, specification, detail_reasons = _extract_detail_cells(page)
            reasons.extend(detail_reasons)
    except Exception as error:
        reasons.append(f"PDF 读取失败：{type(error).__name__}: {error}")

    filename_amount = re.search(r"_(\d+(?:\.\d{2}))_", pdf_path.name)
    if amount is not None and filename_amount:
        named_amount = Decimal(filename_amount.group(1))
        if named_amount != amount:
            reasons.append(f"票面金额 {amount:.2f} 与文件名金额 {named_amount:.2f} 不一致")

    return InvoiceRecord(
        pdf_name=pdf_path.name,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        item_name=item_name,
        specification=specification,
        amount=amount,
        needs_review=bool(reasons),
        review_reasons=reasons,
    )


def render_first_page(pdf_path: Path, output_png: Path, scale: float = 1.6) -> None:
    """输出票面预览，供文字层不可靠时按实际页面人工核对。"""
    import pypdfium2 as pdfium

    output_png.parent.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[0]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        image.save(output_png)
        image.close()
        bitmap.close()
        page.close()
    finally:
        document.close()
