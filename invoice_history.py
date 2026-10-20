from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from invoice_reader import InvoiceRecord
from reimbursement_rules import RULES_DB_PATH


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_history (
            invoice_number TEXT PRIMARY KEY,
            invoice_date TEXT NOT NULL,
            pdf_name TEXT NOT NULL,
            item_name TEXT NOT NULL,
            specification TEXT NOT NULL,
            amount TEXT NOT NULL,
            claimant TEXT NOT NULL,
            student_id TEXT NOT NULL,
            phone TEXT NOT NULL,
            exported_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoice_history_owner "
        "ON invoice_history(student_id, phone, exported_at DESC)"
    )
    connection.commit()
    return connection


def find_history(
    invoice_numbers: list[str],
    db_path: Path = RULES_DB_PATH,
) -> dict[str, dict[str, str]]:
    numbers = list(dict.fromkeys(number for number in invoice_numbers if number))
    if not numbers:
        return {}
    placeholders = ",".join("?" for _ in numbers)
    with closing(_connect(db_path)) as connection:
        rows = connection.execute(
            f"SELECT * FROM invoice_history WHERE invoice_number IN ({placeholders})",
            numbers,
        ).fetchall()
    return {row["invoice_number"]: dict(row) for row in rows}


def list_history(
    student_id: str,
    phone: str,
    db_path: Path = RULES_DB_PATH,
) -> list[dict[str, str]]:
    with closing(_connect(db_path)) as connection:
        rows = connection.execute(
            """SELECT invoice_number, invoice_date, pdf_name, item_name, amount, exported_at
               FROM invoice_history
               WHERE student_id = ? AND phone = ?
               ORDER BY exported_at DESC LIMIT 300""",
            (student_id, phone),
        ).fetchall()
    return [dict(row) for row in rows]


def save_history(
    records: list[InvoiceRecord],
    claimant: str,
    student_id: str,
    phone: str,
    db_path: Path = RULES_DB_PATH,
) -> list[str]:
    numbers = [record.invoice_number for record in records]
    if not numbers or any(not number for number in numbers):
        raise ValueError("登记历史需要至少一张具有发票号码的发票。")
    repeated_in_batch = sorted({number for number in numbers if numbers.count(number) > 1})
    if repeated_in_batch:
        return repeated_in_batch

    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in numbers)
        existing = connection.execute(
            f"SELECT invoice_number FROM invoice_history WHERE invoice_number IN ({placeholders})",
            numbers,
        ).fetchall()
        if existing:
            connection.rollback()
            return sorted(row["invoice_number"] for row in existing)
        connection.executemany(
            """INSERT INTO invoice_history
               (invoice_number, invoice_date, pdf_name, item_name, specification, amount,
                claimant, student_id, phone, exported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    record.invoice_number,
                    record.invoice_date,
                    record.pdf_name,
                    record.item_name,
                    record.specification,
                    str(record.amount or Decimal("0.00")),
                    claimant,
                    student_id,
                    phone,
                    now,
                )
                for record in records
            ],
        )
        connection.commit()
    return []


def undo_history(invoice_number: str, student_id: str, phone: str, db_path: Path = RULES_DB_PATH) -> bool:
    with closing(_connect(db_path)) as connection:
        cursor = connection.execute(
            "DELETE FROM invoice_history WHERE invoice_number = ? AND student_id = ? AND phone = ?",
            (invoice_number, student_id, phone),
        )
        connection.commit()
        return cursor.rowcount == 1
