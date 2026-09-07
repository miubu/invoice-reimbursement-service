from __future__ import annotations

import io
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from archive_reader import extract_pdfs, inspect_archive
from invoice_reader import InvoiceRecord
from reimbursement_rules import ReimbursementRule, classify_reimbursement
from rule_files import resolve_printable_rule_file


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


def build_print_pdf(
    input_paths: list[Path],
    records: tuple[InvoiceRecord, ...],
    fallback_type: str,
    rules: tuple[ReimbursementRule, ...],
    *,
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
    for rule in rules:
        if rule.id not in matched_rule_ids or not rule.template_url.strip():
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
        for invoice_pdf in invoice_pdfs:
            _append_pdf(writer, invoice_pdf, invoice_pdf.name)

    for attachment, label in attachments:
        _append_pdf(writer, attachment, label)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
