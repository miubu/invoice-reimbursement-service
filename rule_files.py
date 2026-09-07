from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote


BUILTIN_RULE_FILE_DIR = Path(__file__).resolve().parent / "builtin_rule_files"
RULE_FILE_DIR = Path(
    os.getenv("RULE_FILE_DIR", str(Path(tempfile.gettempdir()) / "invoice-rule-files"))
)
MAX_RULE_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_RULE_FILE_SUFFIXES = {".docx", ".pdf"}
BUILTIN_DOWNLOAD_NAMES = {
    "courier.docx": "4快递报销说明模板.docx",
    "transport.docx": "3交通费说明模板.docx",
    "cable.docx": "5连接线材与配电辅助配件说明模板.docx",
}


def _validate_content(suffix: str, content: bytes) -> None:
    if not content:
        raise ValueError("上传的说明文件为空。")
    if len(content) > MAX_RULE_FILE_BYTES:
        raise ValueError("说明文件不能超过 10 MB。")
    if suffix == ".docx" and content[:4] not in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
        raise ValueError("文件不是有效的 DOCX。")
    if suffix == ".pdf" and content[:5] != b"%PDF-":
        raise ValueError("文件不是有效的 PDF。")


def save_rule_file(rule_id: str, original_name: str, content: bytes) -> tuple[Path, str]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", rule_id):
        raise ValueError("规则编号无效。")
    source_name = Path((original_name or "").replace("\\", "/")).name
    suffix = Path(source_name).suffix.lower()
    if suffix not in ALLOWED_RULE_FILE_SUFFIXES:
        raise ValueError("说明文件只接受 DOCX 或 PDF。")
    _validate_content(suffix, content)

    stem = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "-", Path(source_name).stem).strip("-")
    stem = stem[:80] or "说明文件"
    filename = f"{rule_id}-{stem}{suffix}"
    RULE_FILE_DIR.mkdir(parents=True, exist_ok=True)
    target = RULE_FILE_DIR / filename
    temporary = RULE_FILE_DIR / f".{filename}.upload"
    temporary.write_bytes(content)
    os.replace(temporary, target)

    # 每条规则只保留最近上传的一个附件，替换操作不会影响内置只读模板。
    for previous in RULE_FILE_DIR.glob(f"{rule_id}-*"):
        if previous != target and previous.is_file():
            previous.unlink(missing_ok=True)
    return target, f"/rule-files/{quote(filename)}"


def resolve_rule_file(filename: str) -> Path | None:
    if not filename or filename != Path(filename).name:
        return None
    for directory in (RULE_FILE_DIR, BUILTIN_RULE_FILE_DIR):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def download_name(filename: str) -> str:
    return BUILTIN_DOWNLOAD_NAMES.get(filename, filename)
