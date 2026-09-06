from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from archive_reader import ArchiveSummary, extract_pdfs, inspect_archive
from invoice_reader import InvoiceRecord, read_invoice
from validator import ValidationResult, validate


@dataclass(frozen=True)
class ProcessingResult:
    summary: ArchiveSummary
    records: tuple[InvoiceRecord, ...]
    validation: ValidationResult


def process_zip(
    zip_path: Path,
    *,
    expected_total: Decimal | None = None,
    max_pdf_count: int = 100,
    max_uncompressed_bytes: int = 200 * 1024 * 1024,
    max_compression_ratio: float = 200.0,
) -> ProcessingResult:
    summary = inspect_archive(
        zip_path,
        max_pdf_count=max_pdf_count,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
    )
    if expected_total is not None:
        summary = replace(summary, expected_total=expected_total)

    with tempfile.TemporaryDirectory(prefix="invoice_service_extract_") as temporary:
        pdf_paths = extract_pdfs(summary, Path(temporary))
        records = tuple(read_invoice(path) for path in pdf_paths)

    return ProcessingResult(
        summary=summary,
        records=records,
        validation=validate(summary, list(records)),
    )
