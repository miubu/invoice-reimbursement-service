from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from archive_reader import extract_pdfs, inspect_archive, locate_zip
from excel_writer import write_excel
from invoice_reader import InvoiceRecord, read_invoice, render_first_page
from validator import validate


DEFAULT_REQUESTED_ZIP = Path.home() / "Downloads" / "QQ邮箱发票" / "_共6张_总金额423.60元.zip"
DEFAULT_OUTPUT = Path.home() / "Downloads" / "QQ邮箱发票" / "发票报销填报表.xlsx"
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PREVIEW_DIR = PROJECT_DIR / "preview"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从电子发票 ZIP 生成报销填报表")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="只打印识别结果并生成票面预览（默认）")
    mode.add_argument("--export", action="store_true", help="校验通过后生成最终 Excel")
    parser.add_argument("--confirm-reviewed", action="store_true", help="确认已人工检查预览结果；导出时必需")
    parser.add_argument("--zip", type=Path, default=DEFAULT_REQUESTED_ZIP, help="发票 ZIP 路径")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="最终 Excel 路径")
    parser.add_argument("--preview-dir", type=Path, default=DEFAULT_PREVIEW_DIR, help="识别结果和页面预览目录")
    parser.add_argument("--fill-date", default="2026/9/6", help="填报日期，格式 yyyy/m/d")
    return parser.parse_args()


def print_records(records: list[InvoiceRecord]) -> None:
    for index, record in enumerate(records, start=1):
        status = "待人工核对" if record.needs_review else "已识别"
        print(f"\n[{index}] {record.pdf_name}")
        print(f"    项目名称：{record.item_name or '【空】'}")
        print(f"    规格型号：{record.specification or '【空】'}")
        print(f"    票面金额：{record.amount:.2f}" if record.amount is not None else "    票面金额：【未识别】")
        print(f"    状态：{status}")
        for reason in record.review_reasons:
            print(f"    - {reason}")


def save_preview(records: list[InvoiceRecord], preview_dir: Path) -> Path:
    preview_dir.mkdir(parents=True, exist_ok=True)
    path = preview_dir / "识别结果.json"
    payload = [record.to_json_dict() for record in records]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_arguments()
    preview_mode = not args.export

    try:
        fill_date = datetime.strptime(args.fill_date, "%Y/%m/%d")
        zip_path = locate_zip(args.zip)
        summary = inspect_archive(zip_path)
        print(f"ZIP：{summary.zip_path}")
        print(f"PDF 数量：{len(summary.pdf_entries)}")

        with tempfile.TemporaryDirectory(prefix="invoice_reimbursement_") as temporary:
            pdf_paths = extract_pdfs(summary, Path(temporary))
            records = [read_invoice(path) for path in pdf_paths]

            if preview_mode:
                pages_dir = args.preview_dir / "pages"
                for index, path in enumerate(pdf_paths, start=1):
                    safe_stem = f"{index:02d}_{path.stem}"
                    render_first_page(path, pages_dir / f"{safe_stem}.png")

        print_records(records)
        result = validate(summary, records)
        print(f"\n票面金额合计：{result.total:.2f}" if result.total is not None else "\n票面金额合计：无法计算")
        if summary.expected_total is not None:
            print(f"ZIP 文件名总金额：{summary.expected_total:.2f}")
        for error in result.errors:
            print(f"错误：{error}", file=sys.stderr)

        preview_path = save_preview(records, args.preview_dir)
        print(f"识别结果：{preview_path}")

        if preview_mode:
            print("当前为预览模式，未生成 Excel。")
            return 1 if result.errors else 0

        if not args.confirm_reviewed:
            print("未提供 --confirm-reviewed，拒绝生成最终 Excel。", file=sys.stderr)
            return 2
        if not result.can_export:
            if result.review_files:
                print("以下 PDF 仍需人工核对，拒绝生成最终 Excel：", file=sys.stderr)
                for filename in result.review_files:
                    print(f"- {filename}", file=sys.stderr)
            return 3

        write_excel(records, args.output, fill_date)
        print(f"已生成：{args.output}")
        return 0
    except Exception as error:
        print(f"程序失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
