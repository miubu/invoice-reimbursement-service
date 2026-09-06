from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from archive_reader import extract_pdfs, inspect_archive
from invoice_reader import InvoiceRecord, read_invoice
from validator import ValidationResult, validate


COUNT_RE = re.compile(r"共\s*(\d+)\s*张")
TOTAL_RE = re.compile(r"总金额\s*(\d+(?:\.\d+)?)\s*元")


@dataclass(frozen=True)
class BatchSummary:
    source_names: tuple[str, ...]
    non_pdf_entries: tuple[str, ...]
    expected_count: int | None
    expected_total: Decimal | None
    uploaded_pdf_count: int
    archive_count: int
    split_pdf_names: tuple[str, ...]


@dataclass(frozen=True)
class ProcessingResult:
    summary: BatchSummary
    records: tuple[InvoiceRecord, ...]
    validation: ValidationResult


def _declared_count(filename: str) -> int | None:
    match = COUNT_RE.search(filename)
    return int(match.group(1)) if match else None


def _declared_total(filename: str) -> Decimal | None:
    match = TOTAL_RE.search(filename)
    return Decimal(match.group(1)) if match else None


def _split_pdf_pages(source: Path, destination: Path) -> tuple[list[Path], bool]:
    """将合并 PDF 按页拆开；单页文件保持原文件名。"""
    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise ValueError(f"PDF 已加密，无法读取：{source.name}")

    page_count = len(reader.pages)
    if page_count < 1:
        raise ValueError(f"PDF 没有页面：{source.name}")
    if page_count == 1:
        return [source], False

    destination.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    for page_number, page in enumerate(reader.pages, start=1):
        target = destination / f"{source.stem}__第{page_number:03d}页.pdf"
        writer = PdfWriter()
        writer.add_page(page)
        with target.open("wb") as output:
            writer.write(output)
        pages.append(target)
    return pages, True


def process_inputs(
    input_paths: list[Path],
    *,
    expected_total: Decimal | None = None,
    max_pdf_count: int = 100,
    max_uncompressed_bytes: int = 200 * 1024 * 1024,
    max_compression_ratio: float = 200.0,
) -> ProcessingResult:
    """处理任意数量的 PDF/ZIP，并把合并 PDF 按页作为独立发票读取。"""
    if not input_paths:
        raise ValueError("没有收到 PDF 或 ZIP 文件。")

    source_names = tuple(path.name for path in input_paths)
    non_pdf_entries: list[str] = []
    pdf_sources: list[Path] = []
    declared_counts: list[int | None] = []
    declared_totals: list[Decimal | None] = []
    uploaded_pdf_count = 0
    archive_count = 0
    uncompressed_total = 0

    with tempfile.TemporaryDirectory(prefix="invoice_service_batch_") as temporary:
        work_dir = Path(temporary)
        for source_index, path in enumerate(input_paths, start=1):
            suffix = path.suffix.lower()
            declared_counts.append(_declared_count(path.name))
            declared_totals.append(_declared_total(path.name))

            if suffix == ".zip":
                archive_count += 1
                archive = inspect_archive(
                    path,
                    max_pdf_count=max_pdf_count,
                    max_uncompressed_bytes=max_uncompressed_bytes,
                    max_compression_ratio=max_compression_ratio,
                )
                uncompressed_total += archive.uncompressed_bytes
                non_pdf_entries.extend(
                    f"{path.name}：{entry}" for entry in archive.non_pdf_entries
                )
                extracted_dir = work_dir / f"archive_{source_index:03d}"
                pdf_sources.extend(extract_pdfs(archive, extracted_dir))
                uploaded_pdf_count += len(archive.pdf_entries)
            elif suffix == ".pdf":
                uploaded_pdf_count += 1
                uncompressed_total += path.stat().st_size
                pdf_sources.append(path)
            else:
                raise ValueError(f"不支持的文件类型：{path.name}")

            if uncompressed_total > max_uncompressed_bytes:
                limit_mb = max_uncompressed_bytes // (1024 * 1024)
                raise ValueError(f"全部文件解压后总大小超过 {limit_mb} MB 限制。")

        page_paths: list[Path] = []
        split_pdf_names: list[str] = []
        for pdf_index, pdf_path in enumerate(pdf_sources, start=1):
            split_dir = work_dir / f"split_{pdf_index:03d}"
            pages, was_split = _split_pdf_pages(pdf_path, split_dir)
            if was_split:
                split_pdf_names.append(pdf_path.name)
            page_paths.extend(pages)
            if len(page_paths) > max_pdf_count:
                raise ValueError(f"拆页后发票页数超过限制 {max_pdf_count}。")

        records = tuple(read_invoice(path) for path in page_paths)

    inferred_count: int | None = None
    if len(input_paths) == 1 and declared_counts[0] is not None:
        inferred_count = declared_counts[0]

    inferred_total: Decimal | None = expected_total
    if inferred_total is None and declared_totals and all(
        total is not None for total in declared_totals
    ):
        inferred_total = sum(
            (total for total in declared_totals if total is not None),
            Decimal("0.00"),
        )

    summary = BatchSummary(
        source_names=source_names,
        non_pdf_entries=tuple(non_pdf_entries),
        expected_count=inferred_count,
        expected_total=inferred_total,
        uploaded_pdf_count=uploaded_pdf_count,
        archive_count=archive_count,
        split_pdf_names=tuple(split_pdf_names),
    )
    return ProcessingResult(
        summary=summary,
        records=records,
        validation=validate(summary, list(records)),
    )


def process_zip(
    zip_path: Path,
    *,
    expected_total: Decimal | None = None,
    max_pdf_count: int = 100,
    max_uncompressed_bytes: int = 200 * 1024 * 1024,
    max_compression_ratio: float = 200.0,
) -> ProcessingResult:
    """兼容原有单 ZIP 调用。"""
    return process_inputs(
        [zip_path],
        expected_total=expected_total,
        max_pdf_count=max_pdf_count,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
    )
