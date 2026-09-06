from __future__ import annotations

import asyncio
import os
import re
import secrets
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from excel_writer import ReimbursementProfile, build_excel_bytes
from processing import ProcessingResult, process_zip


MAX_CONCURRENT_JOBS = max(5, int(os.getenv("MAX_CONCURRENT_JOBS", "10")))
MAX_QUEUED_JOBS = max(0, int(os.getenv("MAX_QUEUED_JOBS", "50")))
QUEUE_TIMEOUT_SECONDS = max(1.0, float(os.getenv("QUEUE_TIMEOUT_SECONDS", "30")))
MAX_UPLOAD_BYTES = max(1, int(os.getenv("MAX_UPLOAD_MB", "50"))) * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = max(1, int(os.getenv("MAX_UNCOMPRESSED_MB", "200"))) * 1024 * 1024
MAX_PDF_COUNT = max(1, int(os.getenv("MAX_PDF_COUNT", "100")))
MAX_COMPRESSION_RATIO = max(1.0, float(os.getenv("MAX_COMPRESSION_RATIO", "200")))
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "")


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
    version="1.0.0",
    description="上传电子发票 ZIP，预览票面原文字段或下载校验后的 Excel。",
)


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
    name = (filename or "invoices.zip").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    if not name.lower().endswith(".zip"):
        raise HTTPException(status_code=415, detail="只接受 .zip 文件。")
    return name[-180:] or "invoices.zip"


async def _save_upload(upload: UploadFile, directory: Path) -> Path:
    target = directory / _safe_upload_name(upload.filename)
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"上传文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 限制。",
                    )
                output.write(chunk)
    finally:
        await upload.close()

    with target.open("rb") as source:
        signature = source.read(4)
    if signature not in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
        raise HTTPException(status_code=415, detail="上传内容不是有效 ZIP。")
    return target


def _parse_total(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        total = Decimal(value.strip()).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise HTTPException(status_code=422, detail="expected_total 必须是有效金额。") from error
    if total < 0:
        raise HTTPException(status_code=422, detail="expected_total 不能为负数。")
    return total


def _serialize(result: ProcessingResult) -> dict:
    validation = result.validation
    return {
        "pdf_count": len(result.records),
        "expected_count": result.summary.expected_count,
        "non_pdf_entries": list(result.summary.non_pdf_entries),
        "amount_total": f"{validation.total:.2f}" if validation.total is not None else None,
        "expected_total": (
            f"{result.summary.expected_total:.2f}"
            if result.summary.expected_total is not None
            else None
        ),
        "errors": list(validation.errors),
        "review_files": list(validation.review_files),
        "can_export": validation.can_export and result.summary.expected_total is not None,
        "records": [record.to_json_dict() for record in result.records],
    }


async def _process_upload(upload: UploadFile, expected_total: str | None) -> ProcessingResult:
    parsed_total = _parse_total(expected_total)
    async with limiter.slot():
        with tempfile.TemporaryDirectory(prefix="invoice_service_request_") as temporary:
            zip_path = await _save_upload(upload, Path(temporary))
            try:
                return await asyncio.to_thread(
                    process_zip,
                    zip_path,
                    expected_total=parsed_total,
                    max_pdf_count=MAX_PDF_COUNT,
                    max_uncompressed_bytes=MAX_UNCOMPRESSED_BYTES,
                    max_compression_ratio=MAX_COMPRESSION_RATIO,
                )
            except HTTPException:
                raise
            except Exception as error:
                raise HTTPException(status_code=422, detail=f"发票处理失败：{error}") from error


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "max_concurrent_jobs_per_worker": limiter.capacity,
        "active_jobs": limiter.active,
        "waiting_jobs": limiter.waiting,
    }


@app.post("/v1/invoices/preview")
async def preview_invoices(
    file: UploadFile = File(...),
    expected_total: str | None = Form(default=None),
    _authenticated: None = Depends(_require_api_key),
) -> JSONResponse:
    result = await _process_upload(file, expected_total)
    return JSONResponse(_serialize(result))


@app.post("/v1/invoices/export")
async def export_invoices(
    file: UploadFile = File(...),
    expected_total: str | None = Form(default=None),
    claimant: str = Form(default="李府鸿"),
    student_id: str = Form(default="12604040"),
    reimbursement_type: str = Form(default="材 料 费"),
    fill_date: str = Form(default="2026/9/6"),
    phone: str = Form(default="13516389370"),
    transmit_invoice: str = Form(default="YES"),
    _authenticated: None = Depends(_require_api_key),
) -> Response:
    result = await _process_upload(file, expected_total)
    payload = _serialize(result)
    if result.summary.expected_total is None:
        raise HTTPException(
            status_code=422,
            detail={"message": "ZIP 文件名没有总金额，请提交 expected_total。", **payload},
        )
    if not result.validation.can_export:
        raise HTTPException(
            status_code=422,
            detail={"message": "校验未通过，未生成 Excel。", **payload},
        )
    try:
        parsed_date = datetime.strptime(fill_date, "%Y/%m/%d")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="fill_date 必须使用 yyyy/m/d 格式。") from error

    profile = ReimbursementProfile(
        claimant=claimant,
        student_id=student_id,
        reimbursement_type=reimbursement_type,
        phone=phone,
        transmit_invoice=transmit_invoice,
    )
    workbook = await asyncio.to_thread(build_excel_bytes, list(result.records), parsed_date, profile)
    filename = "发票报销填报表.xlsx"
    disposition = f"attachment; filename=invoice-reimbursement.xlsx; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )
