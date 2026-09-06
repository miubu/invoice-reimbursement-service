from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZipInfo


@dataclass(frozen=True)
class ArchiveSummary:
    zip_path: Path
    pdf_entries: tuple[ZipInfo, ...]
    non_pdf_entries: tuple[str, ...]
    expected_count: int | None
    expected_total: Decimal | None
    uncompressed_bytes: int = 0


def locate_zip(requested: Path | None = None) -> Path:
    """定位压缩包，兼容用户描述中的目录与实际下载文件名差异。"""
    if requested is not None and requested.is_file():
        return requested.resolve()

    downloads = Path.home() / "Downloads"
    candidates = sorted(downloads.rglob("*423.60*.zip"))
    if requested is not None:
        same_name = [path for path in candidates if path.name == requested.name]
        if same_name:
            return same_name[0].resolve()
    if len(candidates) == 1:
        return candidates[0].resolve()
    if not candidates:
        raise FileNotFoundError("未在 Downloads 中找到文件名含 423.60 的 ZIP。")
    raise RuntimeError("找到多个候选 ZIP，请使用 --zip 指定：\n" + "\n".join(map(str, candidates)))


def inspect_archive(
    zip_path: Path,
    *,
    max_pdf_count: int = 100,
    max_uncompressed_bytes: int = 200 * 1024 * 1024,
    max_compression_ratio: float = 200.0,
) -> ArchiveSummary:
    with ZipFile(zip_path, metadata_encoding="gbk") as archive:
        file_entries = tuple(info for info in archive.infolist() if not info.is_dir())
        pdf_entries = tuple(info for info in file_entries if info.filename.lower().endswith(".pdf"))

    if not pdf_entries:
        raise ValueError("ZIP 中没有 PDF 发票。")
    if len(pdf_entries) > max_pdf_count:
        raise ValueError(f"PDF 数量 {len(pdf_entries)} 超过限制 {max_pdf_count}。")

    uncompressed_total = sum(info.file_size for info in file_entries)
    if uncompressed_total > max_uncompressed_bytes:
        limit_mb = max_uncompressed_bytes // (1024 * 1024)
        raise ValueError(f"ZIP 解压后总大小超过 {limit_mb} MB 限制。")
    for info in file_entries:
        if info.file_size == 0:
            continue
        if info.compress_size == 0:
            raise ValueError(f"ZIP 条目压缩大小异常：{info.filename}")
        ratio = info.file_size / info.compress_size
        if ratio > max_compression_ratio:
            raise ValueError(f"ZIP 条目压缩比异常：{info.filename}")

    count_match = re.search(r"共\s*(\d+)\s*张", zip_path.name)
    total_match = re.search(r"总金额\s*(\d+(?:\.\d+)?)\s*元", zip_path.name)
    return ArchiveSummary(
        zip_path=zip_path,
        pdf_entries=pdf_entries,
        non_pdf_entries=tuple(info.filename for info in file_entries if info not in pdf_entries),
        expected_count=int(count_match.group(1)) if count_match else None,
        expected_total=Decimal(total_match.group(1)) if total_match else None,
        uncompressed_bytes=uncompressed_total,
    )


def extract_pdfs(summary: ArchiveSummary, destination: Path) -> list[Path]:
    """只解压 PDF，并拒绝路径穿越和重名覆盖。"""
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    used_names: set[str] = set()

    with ZipFile(summary.zip_path, metadata_encoding="gbk") as archive:
        for info in summary.pdf_entries:
            archive_name = info.filename.replace("\\", "/")
            pure = PurePosixPath(archive_name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"ZIP 中存在不安全路径：{info.filename}")

            filename = pure.name
            key = filename.casefold()
            if key in used_names:
                raise ValueError(f"ZIP 中存在同名 PDF：{filename}")
            used_names.add(key)

            target = destination / filename
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            outputs.append(target)

    return sorted(outputs, key=lambda path: path.name)
