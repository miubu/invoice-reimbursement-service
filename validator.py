from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from invoice_reader import InvoiceRecord


class ValidationSummary(Protocol):
    expected_count: int | None
    expected_total: Decimal | None


@dataclass(frozen=True)
class ValidationResult:
    total: Decimal | None
    errors: tuple[str, ...]
    review_files: tuple[str, ...]

    @property
    def can_export(self) -> bool:
        return not self.errors and not self.review_files


def validate(summary: ValidationSummary, records: list[InvoiceRecord]) -> ValidationResult:
    errors: list[str] = []
    review_files = tuple(record.pdf_name for record in records if record.needs_review)

    if summary.expected_count is not None and len(records) != summary.expected_count:
        errors.append(f"PDF 数量为 {len(records)}，文件名要求 {summary.expected_count}")

    missing_amount = [record.pdf_name for record in records if record.amount is None]
    if missing_amount:
        errors.append("以下发票未取得价税合计（小写）：" + "、".join(missing_amount))
        total = None
    else:
        total = sum((record.amount for record in records if record.amount is not None), Decimal("0.00"))
        if summary.expected_total is not None and total != summary.expected_total:
            errors.append(f"票面金额合计 {total:.2f}，不等于预期总金额 {summary.expected_total:.2f}")

    return ValidationResult(total=total, errors=tuple(errors), review_files=review_files)
