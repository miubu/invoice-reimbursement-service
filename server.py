from __future__ import annotations

import asyncio
import os
import re
import secrets
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from excel_writer import ReimbursementProfile, build_excel_bytes
from processing import ProcessingResult, process_inputs


MAX_CONCURRENT_JOBS = max(5, int(os.getenv("MAX_CONCURRENT_JOBS", "10")))
MAX_QUEUED_JOBS = max(0, int(os.getenv("MAX_QUEUED_JOBS", "50")))
QUEUE_TIMEOUT_SECONDS = max(1.0, float(os.getenv("QUEUE_TIMEOUT_SECONDS", "30")))
MAX_UPLOAD_BYTES = max(1, int(os.getenv("MAX_UPLOAD_MB", "50"))) * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = max(1, int(os.getenv("MAX_UNCOMPRESSED_MB", "200"))) * 1024 * 1024
MAX_PDF_COUNT = max(1, int(os.getenv("MAX_PDF_COUNT", "100")))
MAX_COMPRESSION_RATIO = max(1.0, float(os.getenv("MAX_COMPRESSION_RATIO", "200")))
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "")
WEB_DIR = Path(__file__).resolve().parent / "web"
SUPPORTED_SUFFIXES = {".pdf", ".zip"}


class JobLimiter:
    def __init__(self, capacity: int, max_waiting: int, timeout: float) -> None:
        self.capacity = capacity
        self.max_waiting = max_waiting
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(capacity)
        self._lock = asyncio.Lock()
        self.active = 0
        self.waiting = 0

    @asynccontextmanager
    async def slot(self):
        async with self._lock:
            if self.waiting >= self.max_waiting and self._semaphore.locked():
                raise HTTPException(status_code=429, detail="服务器处理队列已满，请稍后重试。")
            self.waiting += 1
        try:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=self.timeout)
            except TimeoutError as error:
                raise HTTPException(status_code=503, detail="等待处理超时，请稍后重试。") from error
        finally:
            async with self._lock:
                self.waiting -= 1

        async with self._lock:
            self.active += 1
        try:
            yield
        finally:
            async with self._lock:
                self.active -= 1
            self._semaphore.release()


limiter = JobLimiter(MAX_CONCURRENT_JOBS, MAX_QUEUED_JOBS, QUEUE_TIMEOUT_SECONDS)
app = FastAPI(
    title="电子发票报销填报服务",
    version="2.0.0",
    description="上传一个或多个电子发票 PDF/ZIP，预览票面原文字段并下载校验后的 Excel。",
)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


async def _require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not SERVICE_API_KEY:
        return
    scheme, _, supplied = (authorization or "").partition(" ")
    valid = scheme.lower() == "bearer" and secrets.compare_digest(supplied, SERVICE_API_KEY)
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="缺少或无效的服务访问令牌。",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _safe_upload_name(filename: str | None) -> str:
    name = (filename or "invoices.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型：{name}，只接受 PDF 或 ZIP。")
    return name[-180:] or f"invoices{suffix}"


def _validate_signature(path: Path) -> None:
    with path.open("rb") as source:
        signature = source.read(5)
    suffix = path.suffix.lower()
    if suffix == ".zip" and signature[:4] not in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
        raise HTTPException(status_code=415, detail=f"{path.name} 不是有效 ZIP。")
    if suffix == ".pdf" and signature != b"%PDF-":
        raise HTTPException(status_code=415, detail=f"{path.name} 不是有效 PDF。")


async def _save_uploads(uploads: list[UploadFile], directory: Path) -> list[Path]:
    targets: list[Path] = []
    used_names: set[str] = set()
    total_size = 0
    try:
        for upload in uploads:
            name = _safe_upload_name(upload.filename)
            key = name.casefold()
            if key in used_names:
                raise HTTPException(status_code=422, detail=f"上传列表中存在同名文件：{name}")
            used_names.add(key)

            target = directory / name
            with target.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    total_size += len(chunk)
                    if total_size > MAX_UPLOAD_BYTES:
                        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                        raise HTTPException(status_code=413, detail=f"全部上传文件超过 {limit_mb} MB 限制。")
                    output.write(chunk)
            _validate_signature(target)
            targets.append(target)
    finally:
        for upload in uploads:
            with suppress(Exception):
                await upload.close()
    return targets


def _parse_total(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        total = Decimal(value.strip()).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise HTTPException(status_code=422, detail="预期总金额必须是有效金额。") from error
    if total < 0:
        raise HTTPException(status_code=422, detail="预期总金额不能为负数。")
    return total


def _serialize(result: ProcessingResult) -> dict:
    validation = result.validation
    summary = result.summary
    return {
        "input_count": len(summary.source_names),
        "source_names": list(summary.source_names),
        "uploaded_pdf_count": summary.uploaded_pdf_count,
        "archive_count": summary.archive_count,
        "pdf_count": len(result.records),
        "split_pdf_names": list(summary.split_pdf_names),
        "expected_count": summary.expected_count,
        "non_pdf_entries": list(summary.non_pdf_entries),
        "amount_total": f"{validation.total:.2f}" if validation.total is not None else None,
        "expected_total": f"{summary.expected_total:.2f}" if summary.expected_total is not None else None,
        "errors": list(validation.errors),
        "review_files": list(validation.review_files),
        "can_export": validation.can_export and summary.expected_total is not None,
        "records": [record.to_json_dict() for record in result.records],
    }


def _collect_uploads(files: list[UploadFile] | None, file: UploadFile | None) -> list[UploadFile]:
    uploads = list(files or [])
    if file is not None:
        uploads.append(file)
    if not uploads:
        raise HTTPException(status_code=422, detail="请至少上传一个 PDF 或 ZIP 文件。")
    return uploads


async def _process_uploads(uploads: list[UploadFile], expected_total: str | None) -> ProcessingResult:
    parsed_total = _parse_total(expected_total)
    async with limiter.slot():
        with tempfile.TemporaryDirectory(prefix="invoice_service_request_") as temporary:
            input_paths = await _save_uploads(uploads, Path(temporary))
            try:
                return await asyncio.to_thread(
                    process_inputs,
                    input_paths,
                    expected_total=parsed_total,
                    max_pdf_count=MAX_PDF_COUNT,
                    max_uncompressed_bytes=MAX_UNCOMPRESSED_BYTES,
                    max_compression_ratio=MAX_COMPRESSION_RATIO,
                )
            except HTTPException:
                raise
            except Exception as error:
                raise HTTPException(status_code=422, detail=f"发票处理失败：{error}") from error


def _required_text(value: str, label: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"请填写{label}。")
    if len(cleaned) > max_length:
        raise HTTPException(status_code=422, detail=f"{label}不能超过 {max_length} 个字符。")
    return cleaned


def _parse_date(value: str) -> datetime:
    for date_format in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), date_format)
        except ValueError:
            continue
    raise HTTPException(status_code=422, detail="填报日期必须使用 yyyy/m/d 或 yyyy-mm-dd 格式。")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "version": app.version,
        "max_concurrent_jobs_per_worker": limiter.capacity,
        "active_jobs": limiter.active,
        "waiting_jobs": limiter.waiting,
    }


@app.post("/v1/invoices/preview")
async def preview_invoices(
    files: list[UploadFile] | None = File(default=None, description="一个或多个 PDF/ZIP"),
    file: UploadFile | None = File(default=None, description="兼容旧版单 ZIP 参数"),
    expected_total: str | None = Form(default=None),
    _authenticated: None = Depends(_require_api_key),
) -> JSONResponse:
    result = await _process_uploads(_collect_uploads(files, file), expected_total)
    return JSONResponse(_serialize(result))


@app.post("/v1/invoices/export")
async def export_invoices(
    files: list[UploadFile] | None = File(default=None, description="一个或多个 PDF/ZIP"),
    file: UploadFile | None = File(default=None, description="兼容旧版单 ZIP 参数"),
    expected_total: str | None = Form(default=None),
    claimant: str = Form(default=""),
    student_id: str = Form(default=""),
    reimbursement_type: str = Form(default="材 料 费"),
    fill_date: str = Form(default=""),
    phone: str = Form(default=""),
    transmit_invoice: str = Form(default="YES"),
    _authenticated: None = Depends(_require_api_key),
) -> Response:
    result = await _process_uploads(_collect_uploads(files, file), expected_total)
    payload = _serialize(result)
    if result.summary.expected_total is None:
        raise HTTPException(
            status_code=422,
            detail={"message": "请填写预期总金额，用于与所有发票价税合计核对。", **payload},
        )
    if not result.validation.can_export:
        raise HTTPException(status_code=422, detail={"message": "校验未通过，未生成 Excel。", **payload})

    parsed_date = _parse_date(_required_text(fill_date, "填报日期", 10))
    profile = ReimbursementProfile(
        claimant=_required_text(claimant, "实报人", 50),
        student_id=_required_text(student_id, "学号", 50),
        reimbursement_type=_required_text(reimbursement_type, "报销类型", 50),
        phone=_required_text(phone, "电话", 50),
        transmit_invoice=_required_text(transmit_invoice, "是否传递发票", 20),
    )
    workbook = await asyncio.to_thread(build_excel_bytes, list(result.records), parsed_date, profile)
    filename = "发票报销填报表.xlsx"
    disposition = f"attachment; filename=invoice-reimbursement.xlsx; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )
