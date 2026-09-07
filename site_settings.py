from __future__ import annotations

import io
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from reimbursement_rules import RULES_DB_PATH


SITE_TEXT_DEFAULTS: dict[str, str] = {
    "site_name": "电子发票报销填报",
    "site_tagline": "票面原文识别 · 金额强校验",
    "hero_kicker": "INVOICE WORKSPACE",
    "hero_title": "把发票放进来，核对后下载 Excel",
    "hero_description": "支持同时拖入多个 PDF 或 ZIP。合并 PDF 会按页拆开发票；任何无法可靠识别的字段都会停止导出，不会自行补写。",
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
    now = datetime.now(timezone.utc).isoformat()
    for key, value in SITE_TEXT_DEFAULTS.items():
        connection.execute(
            "INSERT OR IGNORE INTO site_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
    connection.commit()
    return connection


def get_site_texts(db_path: Path = RULES_DB_PATH) -> dict[str, str]:
    with closing(_connect(db_path)) as connection:
        rows = connection.execute("SELECT key, value FROM site_settings").fetchall()
    values = dict(SITE_TEXT_DEFAULTS)
    values.update({row["key"]: row["value"] for row in rows if row["key"] in values})
    return values


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


def save_site_image(slot: str, content: bytes, asset_dir: Path = ASSET_DIR) -> Path:
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("图片不能超过 5 MB。")
    target = asset_path(slot, asset_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            if image.width < 32 or image.height < 32:
                raise ValueError("图片尺寸不能小于 32×32。")
            image.thumbnail((2400, 1400), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            temporary = target.with_suffix(".tmp.webp")
            image.save(temporary, format="WEBP", quality=88, method=6)
            os.replace(temporary, target)
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("图片无法读取，只接受有效的 PNG、JPEG 或 WebP。") from error
    return target


def delete_site_image(slot: str, asset_dir: Path = ASSET_DIR) -> None:
    target = asset_path(slot, asset_dir)
    target.unlink(missing_ok=True)


def site_payload(
    db_path: Path = RULES_DB_PATH,
    asset_dir: Path = ASSET_DIR,
) -> dict[str, Any]:
    assets: dict[str, str | None] = {}
    for slot in sorted(SITE_ASSET_SLOTS):
        path = asset_path(slot, asset_dir)
        assets[slot] = f"/site-assets/{path.name}?v={path.stat().st_mtime_ns}" if path.is_file() else None
    return {"texts": get_site_texts(db_path), "assets": assets}
