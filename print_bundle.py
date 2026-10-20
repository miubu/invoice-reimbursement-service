from __future__ import annotations

import io
import tempfile
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from archive_reader import extract_pdfs, inspect_archive
from invoice_reader import InvoiceRecord
from reimbursement_rules import ReimbursementRule, classify_reimbursement
from rule_files import resolve_printable_rule_file


pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def _collect_invoice_pdfs(
    input_paths: list[Path],
    work_dir: Path,
    *,
    max_pdf_count: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
) -> list[Path]:
    sources: list[Path] = []
    uncompressed_total = 0
    for index, path in enumerate(input_paths, start=1):
        if path.suffix.lower() == ".pdf":
            sources.append(path)
            uncompressed_total += path.stat().st_size
        elif path.suffix.lower() == ".zip":
            archive = inspect_archive(
                path,
                max_pdf_count=max_pdf_count,
                max_uncompressed_bytes=max_uncompressed_bytes,
                max_compression_ratio=max_compression_ratio,
            )
            uncompressed_total += archive.uncompressed_bytes
            sources.extend(extract_pdfs(archive, work_dir / f"archive_{index:03d}"))
        else:
            raise ValueError(f"不支持的文件类型：{path.name}")

        if uncompressed_total > max_uncompressed_bytes:
            limit_mb = max_uncompressed_bytes // (1024 * 1024)
            raise ValueError(f"全部文件解压后总大小超过 {limit_mb} MB 限制。")
        if len(sources) > max_pdf_count:
            raise ValueError(f"PDF 数量超过限制 {max_pdf_count}。")
    return sources


def _append_pdf(writer: PdfWriter, path: Path, label: str) -> None:
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise ValueError(f"PDF 已加密，无法打印：{label}")
    if not reader.pages:
        raise ValueError(f"PDF 没有页面：{label}")
    for page in reader.pages:
        writer.add_page(page)


def _append_invoices_two_up(
    writer: PdfWriter,
    paths: list[Path],
    included_page_indexes: list[int],
) -> None:
    selected = set(included_page_indexes)
    pages = []
    page_index = 0
    for path in paths:
        try:
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise ValueError(f"PDF 已加密，无法打印：{path.name}")
            source_pages = list(reader.pages)
            if not source_pages:
                raise ValueError(f"PDF 没有页面：{path.name}")
        except Exception as error:
            if page_index in selected:
                raise ValueError(f"{path.name} 无法打印，请先从本次移除此项。") from error
            page_index += 1
            continue
        for page in source_pages:
            if page_index in selected:
                if page.rotation:
                    page.transfer_rotation_to_content()
                pages.append(page)
            page_index += 1

    if not selected or not selected.issubset(range(page_index)) or len(pages) != len(included_page_indexes):
        raise ValueError("打印页码与识别结果不一致，请重新预览。")

    a4_width, a4_height = 595.28, 841.89
    margin, gap = 18.0, 10.0
    slot_height = (a4_height - gap) / 2
    for start in range(0, len(pages), 2):
        output_page = PageObject.create_blank_page(width=a4_width, height=a4_height)
        for slot, source_page in enumerate(pages[start:start + 2]):
            source_width = float(source_page.cropbox.width)
            source_height = float(source_page.cropbox.height)
            scale = min(
                (a4_width - 2 * margin) / source_width,
                (slot_height - 2 * margin) / source_height,
            )
            scaled_width = source_width * scale
            scaled_height = source_height * scale
            slot_bottom = slot_height + gap if slot == 0 else 0
            transform = Transformation().translate(
                -float(source_page.cropbox.left), -float(source_page.cropbox.bottom),
            ).scale(scale).translate(
                (a4_width - scaled_width) / 2,
                slot_bottom + (slot_height - scaled_height) / 2,
            )
            output_page.merge_transformed_page(source_page, transform)
        writer.add_page(output_page)


def _wrapped_lines(text: str, width: float, size: float) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and pdfmetrics.stringWidth(candidate, "STSong-Light", size) > width:
                lines.append(current)
                current = character
            else:
                current = candidate
        lines.append(current)
    return lines


def _append_explanations(writer: PdfWriter, explanations: list[dict[str, str]]) -> None:
    for explanation in explanations:
        output = io.BytesIO()
        page = canvas.Canvas(output, pagesize=A4)
        width, height = A4

        def start_page(continued: bool = False) -> float:
            page.setFont("STSong-Light", 20)
            title = f"{explanation['title']}说明" + ("（续）" if continued else "")
            y = height - 62
            for line in _wrapped_lines(title, width - 116, 20):
                page.drawCentredString(width / 2, y, line)
                y -= 28
            return y - 24

        y = start_page()
        sections = (
            ("发票项目名称及明细", explanation["details"]),
            ("说明内容", explanation["note"]),
        )
        for heading, body in sections:
            if y < 145:
                page.showPage()
                y = start_page(True)
            page.setFont("STSong-Light", 12)
            page.drawString(58, y, heading)
            y -= 26
            page.setFont("STSong-Light", 11)
            for line in _wrapped_lines(body, width - 116, 11):
                if y < 115:
                    page.showPage()
                    y = start_page(True)
                    page.setFont("STSong-Light", 11)
                page.drawString(58, y, line)
                y -= 20
            y -= 18

        if y < 125:
            page.showPage()
            y = start_page(True)
        page.setFont("STSong-Light", 11)
        page.drawRightString(width - 58, 92, f"说明人：{explanation['claimant']}")
        page.drawRightString(width - 58, 68, f"日期：{explanation['date']}")
        page.save()
        output.seek(0)
        for generated_page in PdfReader(output, strict=False).pages:
            writer.add_page(generated_page)


def build_print_pdf(
    input_paths: list[Path],
    records: tuple[InvoiceRecord, ...],
    fallback_type: str,
    rules: tuple[ReimbursementRule, ...],
    *,
    included_page_indexes: list[int] | None = None,
    explanations: list[dict[str, str]] | None = None,
    max_pdf_count: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
) -> bytes:
    """合并全部发票页，并在末尾附上命中规则对应的说明 PDF。"""
    matched_rule_ids: set[str] = set()
    for record in records:
        result = classify_reimbursement(record.item_name, fallback_type, rules)
        matched_rule_ids.update(result.matched_rule_ids)

    attachments: list[tuple[Path, str]] = []
    seen_attachments: set[Path] = set()
    unavailable: list[str] = []
    explained_rule_ids = {item["rule_id"] for item in explanations or []}
    for rule in rules:
        if rule.id not in matched_rule_ids or not rule.template_url.strip():
            continue
        if rule.id in explained_rule_ids:
            continue
        attachment = resolve_printable_rule_file(rule.template_url)
        if attachment is None:
            unavailable.append(rule.title or rule.id)
            continue
        resolved = attachment.resolve()
        if resolved not in seen_attachments:
            seen_attachments.add(resolved)
            attachments.append((attachment, rule.title or attachment.name))

    if unavailable:
        names = "、".join(unavailable)
        raise ValueError(
            f"以下命中规则的说明文件不能合并打印：{names}。"
            "请在管理员后台为这些规则上传 PDF 文件。"
        )

    writer = PdfWriter()
    with tempfile.TemporaryDirectory(prefix="invoice_print_bundle_") as temporary:
        invoice_pdfs = _collect_invoice_pdfs(
            input_paths,
            Path(temporary),
            max_pdf_count=max_pdf_count,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        )
        selected = included_page_indexes
        if selected is None:
            selected = list(range(sum(len(PdfReader(str(path), strict=False).pages) for path in invoice_pdfs)))
        _append_invoices_two_up(writer, invoice_pdfs, selected)

    _append_explanations(writer, explanations or [])
    for attachment, label in attachments:
        _append_pdf(writer, attachment, label)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
