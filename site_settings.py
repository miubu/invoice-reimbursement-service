from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from reimbursement_rules import RULES_DB_PATH


SITE_TEXT_DEFAULTS: dict[str, str] = {
    "site_name": "电子发票报销填报",
    "site_tagline": "电子发票识别与报销",
    "hero_kicker": "INVOICE WORKSPACE",
    "hero_title": "把发票放进来，核对后下载 Excel",
    "hero_description": "拖入 PDF 或 ZIP，合并的 PDF 会自动分页。你可以逐张修改并确认识别结果，再下载报销表或打印发票和说明。",
    "upload_title": "选择发票文件",
    "upload_primary": "拖动文件到这里，或点击选择",
    "upload_secondary": "可一次选择四五张 PDF，也可以混合选择 PDF 和 ZIP",
    "profile_title": "填写报销信息",
    "action_title": "先预览，再导出",
    "action_description": "预览会显示每张发票的票面原文字段和校验状态。",
    "preview_button": "识别并预览",
    "print_button": "一键打印全部发票及说明",
    "export_button": "下载 Excel",
}
SITE_ASSET_SLOTS = {"logo", "hero"}
ASSET_DIR = Path(os.getenv("SITE_ASSET_DIR", str(RULES_DB_PATH.parent / "site-assets")))


NoticePage = Literal["home", "invoice", "travel"]


class NoticeText(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=6000)

    @field_validator("title")
    @classmethod
    def single_line_title(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("标题请写在一行内。")
        return value

    @field_validator("body")
    @classmethod
    def normalize_lines(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    def published(self) -> dict[str, str]:
        revision = hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()
        return {**self.model_dump(), "revision": revision}


NOTICE_DEFAULTS: dict[NoticePage, NoticeText] = {
    "home": NoticeText(title="选择适合本次报销的入口", body=(
        "整理采购、快递等电子发票，请进入「发票报销」。填写出差行程、交通和住宿费用，请进入「差旅报销」。\n\n"
        "开始前请备好本次票据和相关凭证，并核对报销人信息。"
    )),
    "invoice": NoticeText(title="发票报销注意事项", body=(
        "可同时选择多个发票 PDF 或 ZIP。识别后请对照票面核对项目名称、规格型号、发票号码和价税合计。规格型号栏为空时留空。\n\n"
        "需要补充说明的发票，请先填写实际用途再打印。完成报销后，点击「登记已报销」保存历史。"
    )),
    "travel": NoticeText(title="差旅报销注意事项", body=(
        "请准备本次出差的交通、住宿发票和相关凭证，核对出差人、起止日期、行程及金额。\n\n"
        "草稿和附件保存在当前浏览器。离开共用电脑前，请先下载需要的报销表和附件，再清除本机数据。"
    )),
}


class ImageLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fit: Literal["contain", "cover"]
    zoom: int = Field(ge=100, le=250)
    x: int = Field(ge=0, le=100)
    y: int = Field(ge=0, le=100)


DEFAULT_IMAGE_LAYOUT = ImageLayout(fit="contain", zoom=100, x=50, y=50)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS site_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    keys = {row[0] for row in connection.execute("SELECT key FROM site_settings")}
    missing = SITE_TEXT_DEFAULTS.keys() - keys
    if missing:
        now = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            "INSERT OR IGNORE INTO site_settings (key, value, updated_at) VALUES (?, ?, ?)",
            [(key, SITE_TEXT_DEFAULTS[key], now) for key in missing],
        )
        connection.commit()
    return connection


def _read_settings(db_path: Path) -> dict[str, str]:
    with closing(_connect(db_path)) as connection:
        return dict(connection.execute("SELECT key, value FROM site_settings"))


def get_site_texts(db_path: Path = RULES_DB_PATH) -> dict[str, str]:
    settings = _read_settings(db_path)
    return {key: settings[key] for key in SITE_TEXT_DEFAULTS}


def _notice_from_value(page: NoticePage, value: str | None) -> dict[str, str]:
    notice = NoticeText.model_validate_json(value) if value is not None else NOTICE_DEFAULTS[page]
    return notice.published()


def get_notice(page: NoticePage, db_path: Path = RULES_DB_PATH) -> dict[str, str]:
    with closing(_connect(db_path)) as connection:
        row = connection.execute("SELECT value FROM site_settings WHERE key = ?", (f"notice.{page}",)).fetchone()
    return _notice_from_value(page, row[0] if row else None)


def save_notice(page: NoticePage, notice: NoticeText, db_path: Path = RULES_DB_PATH) -> dict[str, str]:
    with closing(_connect(db_path)) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO site_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (f"notice.{page}", notice.model_dump_json(), datetime.now(timezone.utc).isoformat()),
        )
    return notice.published()


def update_site_texts(values: dict[str, Any], db_path: Path = RULES_DB_PATH) -> dict[str, str]:
    unknown = set(values) - set(SITE_TEXT_DEFAULTS)
    if unknown:
        raise ValueError("包含不能修改的界面字段：" + "、".join(sorted(unknown)))

    cleaned: dict[str, str] = {}
    for key in SITE_TEXT_DEFAULTS:
        value = str(values.get(key, "")).strip()
        if not value:
            raise ValueError(f"{key} 不能为空。")
        limit = 500 if key in {"hero_description", "action_description"} else 100
        if len(value) > limit:
            raise ValueError(f"{key} 不能超过 {limit} 个字符。")
        cleaned[key] = value

    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for key, value in cleaned.items():
            connection.execute(
                "UPDATE site_settings SET value = ?, updated_at = ? WHERE key = ?",
                (value, now, key),
            )
        connection.commit()
    return get_site_texts(db_path)


def asset_path(slot: str, asset_dir: Path = ASSET_DIR) -> Path:
    if slot not in SITE_ASSET_SLOTS:
        raise ValueError("不支持的图片位置。")
    return asset_dir / f"{slot}.webp"


def _store_layout(connection: sqlite3.Connection, slot: str, layout: ImageLayout) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO site_settings (key, value, updated_at) VALUES (?, ?, ?)",
        (f"image_layout.{slot}", layout.model_dump_json(), datetime.now(timezone.utc).isoformat()),
    )


def update_image_layout(slot: str, layout: ImageLayout, db_path: Path = RULES_DB_PATH) -> None:
    if slot not in SITE_ASSET_SLOTS:
        raise ValueError("不支持的图片位置。")
    with closing(_connect(db_path)) as connection, connection:
        _store_layout(connection, slot, layout)


def save_site_image(
    slot: str, content: bytes, asset_dir: Path = ASSET_DIR, *,
    layout: ImageLayout | None = None, db_path: Path = RULES_DB_PATH,
) -> Path:
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("图片不能超过 5 MB。")
    target = asset_path(slot, asset_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        temporary = Path(staging) / target.name
        try:
            formats = ("PNG", "JPEG", "WEBP")
            with Image.open(io.BytesIO(content), formats=formats) as image:
                image.verify()
            with Image.open(io.BytesIO(content), formats=formats) as image:
                image = ImageOps.exif_transpose(image)
                if image.width < 32 or image.height < 32:
                    raise ValueError("图片尺寸不能小于 32×32。")
                image.thumbnail((2400, 1400), Image.Resampling.LANCZOS)
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                image.save(temporary, format="WEBP", quality=88, method=6)
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as error:
            raise ValueError("图片无法读取，只接受有效的 PNG、JPEG 或 WebP。") from error
        # 各个 Docker worker 先各自编码，再串行发布图片和构图。
        with closing(_connect(db_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if layout is not None:
                _store_layout(connection, slot, layout)
            os.replace(temporary, target)
    return target


def delete_site_image(slot: str, asset_dir: Path = ASSET_DIR, db_path: Path = RULES_DB_PATH) -> None:
    target = asset_path(slot, asset_dir)
    with closing(_connect(db_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM site_settings WHERE key = ?", (f"image_layout.{slot}",))
        target.unlink(missing_ok=True)


def site_payload(
    db_path: Path = RULES_DB_PATH,
    asset_dir: Path = ASSET_DIR,
    *, include_notices: bool = False,
) -> dict[str, Any]:
    assets: dict[str, str | None] = {}
    settings = _read_settings(db_path)
    layouts = {}
    for slot in sorted(SITE_ASSET_SLOTS):
        path = asset_path(slot, asset_dir)
        try:
            assets[slot] = f"/site-assets/{path.name}?v={path.stat().st_mtime_ns}"
        except FileNotFoundError:
            assets[slot] = None
        saved = settings.get(f"image_layout.{slot}")
        layout = ImageLayout.model_validate_json(saved) if saved else DEFAULT_IMAGE_LAYOUT
        layouts[slot] = layout.model_dump()
    payload = {"texts": {key: settings[key] for key in SITE_TEXT_DEFAULTS}, "assets": assets, "image_layouts": layouts}
    if include_notices:
        payload["notices"] = {page: _notice_from_value(page, settings.get(f"notice.{page}")) for page in NOTICE_DEFAULTS}
    return payload
