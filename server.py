from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import secrets
import tempfile
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from excel_writer import ReimbursementProfile, build_excel_bytes
from print_bundle import build_print_pdf
from processing import ProcessingResult, process_inputs
from reimbursement_rules import (
    REIMBURSEMENT_TYPES,
    ReimbursementRule,
    classify_reimbursement,
    create_rule,
    delete_rule,
    get_rules,
    normalize_reimbursement_type,
    update_rule,
)
from rule_files import download_name, resolve_rule_file, save_rule_file
from site_settings import (
    ASSET_DIR,
    SITE_ASSET_SLOTS,
    delete_site_image,
    save_site_image,
    site_payload,
    update_site_texts,
)


MAX_CONCURRENT_JOBS = max(5, int(os.getenv("MAX_CONCURRENT_JOBS", "10")))
MAX_QUEUED_JOBS = max(0, int(os.getenv("MAX_QUEUED_JOBS", "50")))
QUEUE_TIMEOUT_SECONDS = max(1.0, float(os.getenv("QUEUE_TIMEOUT_SECONDS", "30")))
MAX_UPLOAD_BYTES = max(1, int(os.getenv("MAX_UPLOAD_MB", "50"))) * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = max(1, int(os.getenv("MAX_UNCOMPRESSED_MB", "200"))) * 1024 * 1024
MAX_PDF_COUNT = max(1, int(os.getenv("MAX_PDF_COUNT", "100")))
MAX_COMPRESSION_RATIO = max(1.0, float(os.getenv("MAX_COMPRESSION_RATIO", "200")))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
WEB_DIR = Path(__file__).resolve().parent / "web"
SUPPORTED_SUFFIXES = {".pdf", ".zip"}
CONFIRMABLE_REVIEW_REASON = re.compile(
    r"^识别到 \d+ 个不同的非折扣项目，无法安全合并为一行$"
)


class RulePayload(BaseModel):
    title: str = ""
    description: str = ""
    reimbursement_type: str = ""
    keywords: list[str] = Field(default_factory=list)
    remark: str = ""
    template_url: str = ""
    enabled: bool = True


class LegacyRulePayload(RulePayload):
    id: str


class LegacyRulesUpdateBody(BaseModel):
    rules: list[LegacyRulePayload]


class SiteTextUpdateBody(BaseModel):
    texts: dict[str, str]


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
    version="3.4.0",
    description="上传一个或多个电子发票 PDF/ZIP，预览票面原文字段、下载 Excel，或合并发票和说明文件后一键打印。金额合计不再核销。",
)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


def _require_admin(authorization: str | None) -> None:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="管理员密码尚未在服务器环境变量中设置。")
    scheme, _, encoded = (authorization or "").partition(" ")
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        username, separator, password = decoded.partition(":")
    except (binascii.Error, UnicodeDecodeError):
        username = password = separator = ""
    valid = (
        scheme.lower() == "basic"
        and bool(separator)
        and secrets.compare_digest(username, ADMIN_USERNAME)
        and secrets.compare_digest(password, ADMIN_PASSWORD)
    )
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="管理员账号或密码错误。",
            headers={"WWW-Authenticate": 'Basic realm="invoice-admin", charset="UTF-8"'},
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


def _serialize(
    result: ProcessingResult,
    fallback_type: str,
    rules: tuple[ReimbursementRule, ...],
    confirmed_review_ids: set[str] | None = None,
) -> dict:
    validation = result.validation
    summary = result.summary
    records = []
    confirmed_ids = confirmed_review_ids or set()
    unresolved_review_files: list[str] = []
    for index, record in enumerate(result.records, start=1):
        record_id = f"invoice-{index:03d}"
        confirmable = _review_is_confirmable(record)
        confirmed = confirmable and record_id in confirmed_ids
        if record.needs_review and not confirmed:
            unresolved_review_files.append(record.pdf_name)
        item = record.to_json_dict()
        classification = classify_reimbursement(record.item_name, fallback_type, rules)
        item.update({
            "record_id": record_id,
            "review_confirmable": confirmable,
            "review_confirmed": confirmed,
            "reimbursement_type": classification.reimbursement_type,
            "remarks": list(classification.remarks),
            "template_urls": list(classification.template_urls),
            "matched_rule_ids": list(classification.matched_rule_ids),
        })
        records.append(item)
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
        "errors": list(validation.errors),
        "review_files": unresolved_review_files,
        "can_export": (
            not validation.errors
            and not unresolved_review_files
        ),
        "records": records,
    }


def _rules_with_public_urls(
    rules: tuple[ReimbursementRule, ...],
    request: Request,
) -> tuple[ReimbursementRule, ...]:
    base_url = str(request.base_url).rstrip("/")
    return tuple(
        replace(rule, template_url=f"{base_url}{rule.template_url}")
        if rule.template_url.startswith("/")
        else rule
        for rule in rules
    )


def _review_is_confirmable(record) -> bool:
    return (
        record.needs_review
        and record.amount is not None
        and bool(record.item_name)
        and bool(record.review_reasons)
        and all(CONFIRMABLE_REVIEW_REASON.fullmatch(reason) for reason in record.review_reasons)
    )


def _parse_confirmed_review_ids(value: str) -> set[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail="人工核对记录格式无效，请重新预览。") from error
    if not isinstance(parsed, list) or len(parsed) > MAX_PDF_COUNT:
        raise HTTPException(status_code=422, detail="人工核对记录格式无效，请重新预览。")
    confirmed_ids = {str(value) for value in parsed}
    if any(not re.fullmatch(r"invoice-\d{3}", record_id) for record_id in confirmed_ids):
        raise HTTPException(status_code=422, detail="人工核对记录包含无效编号，请重新预览。")
    return confirmed_ids


def _collect_uploads(files: list[UploadFile] | None, file: UploadFile | None) -> list[UploadFile]:
    uploads = list(files or [])
    if file is not None:
        uploads.append(file)
    if not uploads:
        raise HTTPException(status_code=422, detail="请至少上传一个 PDF 或 ZIP 文件。")
    return uploads


async def _process_uploads(uploads: list[UploadFile]) -> ProcessingResult:
    async with limiter.slot():
        with tempfile.TemporaryDirectory(prefix="invoice_service_request_") as temporary:
            input_paths = await _save_uploads(uploads, Path(temporary))
            try:
                return await asyncio.to_thread(
                    process_inputs,
                    input_paths,
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


def _validate_transmit_invoice(value: str) -> str:
    cleaned = value.strip().upper()
    if cleaned not in {"YES", "NO"}:
        raise HTTPException(status_code=422, detail="是否传递发票只能选择 YES 或 NO。")
    return cleaned


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_home() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "admin.html").read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "version": app.version,
        "max_concurrent_jobs_per_worker": limiter.capacity,
        "active_jobs": limiter.active,
        "waiting_jobs": limiter.waiting,
    }


@app.get("/v1/site")
async def public_site_settings() -> dict:
    return site_payload()


@app.get("/site-assets/{filename}", include_in_schema=False)
async def public_site_asset(filename: str) -> FileResponse:
    if filename not in {f"{slot}.webp" for slot in SITE_ASSET_SLOTS}:
        raise HTTPException(status_code=404, detail="图片不存在。")
    path = ASSET_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在。")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "no-cache"})


@app.get("/rule-files/{filename}", include_in_schema=False)
async def public_rule_file(filename: str) -> FileResponse:
    path = resolve_rule_file(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="说明文件不存在。")
    media_type = (
        "application/pdf"
        if path.suffix.lower() == ".pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=download_name(filename),
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/v1/invoices/preview")
async def preview_invoices(
    request: Request,
    files: list[UploadFile] | None = File(default=None, description="一个或多个 PDF/ZIP"),
    file: UploadFile | None = File(default=None, description="兼容旧版单 ZIP 参数"),
    expected_total: str | None = Form(default=None, description="兼容旧版参数，服务不再核对金额合计"),
    reimbursement_type: str = Form(default="材料费"),
) -> JSONResponse:
    try:
        fallback_type = normalize_reimbursement_type(reimbursement_type)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    rules = _rules_with_public_urls(get_rules(), request)
    result = await _process_uploads(_collect_uploads(files, file))
    return JSONResponse(_serialize(result, fallback_type, rules))


@app.post("/v1/invoices/export")
async def export_invoices(
    request: Request,
    files: list[UploadFile] | None = File(default=None, description="一个或多个 PDF/ZIP"),
    file: UploadFile | None = File(default=None, description="兼容旧版单 ZIP 参数"),
    expected_total: str | None = Form(default=None, description="兼容旧版参数，服务不再核对金额合计"),
    claimant: str = Form(default=""),
    student_id: str = Form(default=""),
    reimbursement_type: str = Form(default="材料费"),
    fill_date: str = Form(default=""),
    phone: str = Form(default=""),
    transmit_invoice: str = Form(default="YES"),
    confirmed_review_ids: str = Form(default="[]"),
) -> Response:
    try:
        fallback_type = normalize_reimbursement_type(reimbursement_type)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    rules = _rules_with_public_urls(get_rules(), request)
    confirmed_ids = _parse_confirmed_review_ids(confirmed_review_ids)
    result = await _process_uploads(_collect_uploads(files, file))
    confirmable_ids = {
        f"invoice-{index:03d}"
        for index, record in enumerate(result.records, start=1)
        if _review_is_confirmable(record)
    }
    if not confirmed_ids.issubset(confirmable_ids):
        raise HTTPException(status_code=422, detail="发票内容已变化或确认记录无效，请重新预览并核对。")
    payload = _serialize(result, fallback_type, rules, confirmed_ids)
    if not payload["can_export"]:
        raise HTTPException(status_code=422, detail={"message": "仍有需要人工核对的票面字段，未生成 Excel。", **payload})

    parsed_date = _parse_date(_required_text(fill_date, "填报日期", 10))
    profile = ReimbursementProfile(
        claimant=_required_text(claimant, "实报人", 50),
        student_id=_required_text(student_id, "学号", 50),
        reimbursement_type=fallback_type,
        phone=_required_text(phone, "电话", 50),
        transmit_invoice=_validate_transmit_invoice(transmit_invoice),
    )
    workbook = await asyncio.to_thread(
        build_excel_bytes,
        list(result.records),
        parsed_date,
        profile,
        rules,
        confirmed_ids,
    )
    filename = "发票报销填报表.xlsx"
    disposition = f"attachment; filename=invoice-reimbursement.xlsx; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


@app.post("/v1/invoices/print")
async def print_invoices(
    files: list[UploadFile] | None = File(default=None, description="一个或多个 PDF/ZIP"),
    file: UploadFile | None = File(default=None, description="兼容旧版单 ZIP 参数"),
    reimbursement_type: str = Form(default="材料费"),
) -> Response:
    try:
        fallback_type = normalize_reimbursement_type(reimbursement_type)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    rules = get_rules()
    uploads = _collect_uploads(files, file)
    async with limiter.slot():
        with tempfile.TemporaryDirectory(prefix="invoice_service_print_") as temporary:
            input_paths = await _save_uploads(uploads, Path(temporary))
            try:
                result = await asyncio.to_thread(
                    process_inputs,
                    input_paths,
                    max_pdf_count=MAX_PDF_COUNT,
                    max_uncompressed_bytes=MAX_UNCOMPRESSED_BYTES,
                    max_compression_ratio=MAX_COMPRESSION_RATIO,
                )
                printable_pdf = await asyncio.to_thread(
                    build_print_pdf,
                    input_paths,
                    result.records,
                    fallback_type,
                    rules,
                    max_pdf_count=MAX_PDF_COUNT,
                    max_uncompressed_bytes=MAX_UNCOMPRESSED_BYTES,
                    max_compression_ratio=MAX_COMPRESSION_RATIO,
                )
            except HTTPException:
                raise
            except Exception as error:
                raise HTTPException(status_code=422, detail=f"打印文件生成失败：{error}") from error

    filename = "发票及报销说明.pdf"
    disposition = f"inline; filename=invoices-and-notes.pdf; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=printable_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )


@app.get("/v1/admin/rules")
async def admin_get_rules(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    return {
        "rules": [rule.to_dict() for rule in get_rules()],
        "reimbursement_types": list(REIMBURSEMENT_TYPES),
    }


@app.post("/v1/admin/rules")
async def admin_create_rule(
    body: RulePayload,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    try:
        rule = create_rule(body.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"rule": rule.to_dict()}


@app.put("/v1/admin/rules")
async def admin_update_rules_legacy(
    body: LegacyRulesUpdateBody,
    authorization: str | None = Header(default=None),
) -> dict:
    """兼容旧版管理页面的批量保存请求。"""
    _require_admin(authorization)
    current = {rule.id: rule for rule in get_rules()}
    try:
        for item in body.rules:
            payload = item.model_dump(exclude={"id"})
            # 旧页面没有提交名称和说明，保留数据库中的原值。
            existing = current.get(item.id)
            if existing:
                payload["title"] = payload.get("title") or existing.title
                payload["description"] = payload.get("description") or existing.description
            update_rule(item.id, payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "rules": [rule.to_dict() for rule in get_rules()],
        "reimbursement_types": list(REIMBURSEMENT_TYPES),
    }


@app.put("/v1/admin/rules/{rule_id}")
async def admin_update_rule(
    rule_id: str,
    body: RulePayload,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    try:
        rule = update_rule(rule_id, body.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"rule": rule.to_dict()}


@app.post("/v1/admin/rules/{rule_id}/file")
async def admin_upload_rule_file(
    rule_id: str,
    attachment: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    existing = next((rule for rule in get_rules() if rule.id == rule_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="规则不存在或已被删除。")
    content = await attachment.read(10 * 1024 * 1024 + 1)
    await attachment.close()
    try:
        _, template_url = save_rule_file(rule_id, attachment.filename or "", content)
        payload = existing.to_dict()
        payload.pop("id", None)
        payload.pop("priority", None)
        payload["template_url"] = template_url
        updated = update_rule(rule_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"rule": updated.to_dict()}


@app.delete("/v1/admin/rules/{rule_id}")
async def admin_delete_rule(
    rule_id: str,
    authorization: str | None = Header(default=None),
) -> Response:
    _require_admin(authorization)
    try:
        delete_rule(rule_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(status_code=204)


@app.get("/v1/admin/site")
async def admin_get_site(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    return site_payload()


@app.put("/v1/admin/site")
async def admin_update_site(
    body: SiteTextUpdateBody,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    try:
        update_site_texts(body.texts)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return site_payload()


@app.post("/v1/admin/site/image")
async def admin_upload_site_image(
    slot: str = Form(...),
    image: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=415, detail="只接受 PNG、JPEG 或 WebP 图片。")
    content = await image.read(5 * 1024 * 1024 + 1)
    await image.close()
    try:
        save_site_image(slot, content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return site_payload()


@app.delete("/v1/admin/site/image/{slot}")
async def admin_delete_site_image(
    slot: str,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    try:
        delete_site_image(slot)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return site_payload()
