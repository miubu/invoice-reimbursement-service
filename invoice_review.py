from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from processing import ProcessingResult
from reimbursement_rules import ReimbursementRule, classify_reimbursement
from validator import validate


def apply_review(
    result: ProcessingResult, edits_json: str, confirmed_ids: set[str],
) -> tuple[ProcessingResult, list[int], set[str]]:
    if len(edits_json) > 2_000_000:
        raise ValueError("人工修改内容过大。")
    edits = json.loads(edits_json or "[]")
    if not isinstance(edits, list) or len(edits) > len(result.records):
        raise ValueError("人工修改记录格式无效，请重新预览。")
    valid_ids = {f"invoice-{index:03d}" for index in range(1, len(result.records) + 1)}
    if not confirmed_ids.issubset(valid_ids):
        raise ValueError("人工确认记录已失效，请重新预览。")
    by_id = {}
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("人工修改记录格式无效。")
        record_id = edit.get("record_id")
        if not isinstance(record_id, str) or record_id not in valid_ids or record_id in by_id:
            raise ValueError("人工修改包含无效或重复的发票编号。")
        by_id[record_id] = edit

    records, indexes, confirmed = [], [], set()
    for index, original in enumerate(result.records):
        record_id = f"invoice-{index + 1:03d}"
        edit = by_id.get(record_id, {})
        if edit.get("excluded") is True:
            continue
        if edit and (edit.get("pdf_name") != original.pdf_name or edit.get("source_digest") != original.source_digest):
            raise ValueError("上传文件发生变化，请重新预览。")
        values = {}
        for field, limit in (("item_name", 6000), ("specification", 3000),
                             ("invoice_number", 30), ("invoice_date", 30)):
            value = edit.get(field, getattr(original, field))
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f"{original.pdf_name} 的字段格式或长度无效。")
            values[field] = value.strip()
        raw_amount = edit.get("amount", str(original.amount) if original.amount is not None else "")
        if len(str(raw_amount)) > 30:
            raise ValueError(f"{original.pdf_name} 的金额过长。")
        try:
            amount = Decimal(str(raw_amount)) if raw_amount not in (None, "") else None
        except InvalidOperation as error:
            raise ValueError(f"{original.pdf_name} 的金额无效。") from error
        if amount is not None and (not amount.is_finite() or amount < 0 or amount > Decimal("999999999.99")
                                   or amount != amount.quantize(Decimal("0.01"))):
            raise ValueError(f"{original.pdf_name} 的金额须为非负数，最多两位小数。")
        changed = amount != original.amount or any(values[key] != getattr(original, key) for key in values)
        is_confirmed = edit.get("confirmed") is True or record_id in confirmed_ids
        reasons = list(original.review_reasons)
        if changed and not is_confirmed:
            reasons.append("修改后请勾选已对照票面确认")
        if is_confirmed:
            if not values["item_name"] or amount is None or not re.fullmatch(r"\d{8,30}", values["invoice_number"]):
                raise ValueError(f"{original.pdf_name}：请补齐项目名称、金额和8至30位发票号码后再确认。")
            reasons = []
        record = replace(original, **values, amount=amount, needs_review=bool(reasons), review_reasons=reasons)
        records.append(record)
        indexes.append(index)
        if is_confirmed:
            confirmed.add(f"invoice-{len(records):03d}")
    if not records:
        raise ValueError("请至少保留一张发票。")
    # 网页以用户本次保留的发票为准，ZIP 文件名中的原张数不限制人工处理。
    summary = replace(result.summary, expected_count=None)
    return replace(result, summary=summary, records=tuple(records), validation=validate(summary, records)), indexes, confirmed


EXPLANATION_NOTES = {
    "courier": "上述快递费用用于【请填写邮寄内容、用途及快递单号】，属于科研项目支出，特此说明。",
    "transport": "上述市内交通费用用于【请填写往返地点、出行事由及日期】，属于科研项目支出，特此说明。",
    "cable": "上述连接线材与配电辅助配件用于【请填写实验用途及连接对象】，属于科研项目支出，特此说明。",
}


def explanation_groups(records, fallback_type: str, rules: tuple[ReimbursementRule, ...]) -> list[dict]:
    groups = []
    for rule in rules:
        if not rule.enabled or not (rule.template_url or rule.id in EXPLANATION_NOTES):
            continue
        matched = [record for record in records
                   if rule.id in classify_reimbursement(record.item_name, fallback_type, rules).matched_rule_ids]
        if not matched:
            continue
        details = "\n".join(
            f"发票号码：{record.invoice_number or '待补充'}；项目名称：{record.item_name}；"
            f"规格型号：{record.specification or '（票面空白）'}；价税合计：{record.amount if record.amount is not None else '待补充'}元"
            for record in matched
        )
        groups.append({"rule_id": rule.id, "title": rule.title, "details": details,
                       "note": EXPLANATION_NOTES.get(rule.id, "上述费用用于【请填写具体用途和事由】，特此说明。")})
    return groups


def prepare_explanations(records, fallback_type, rules, value: str, claimant: str, date: str) -> list[dict]:
    if len(value) > 200_000:
        raise ValueError("说明内容过大。")
    supplied = json.loads(value or "[]")
    if not isinstance(supplied, list) or any(not isinstance(item, dict) for item in supplied):
        raise ValueError("说明内容格式无效。")
    if any(not isinstance(item.get("rule_id"), str) for item in supplied):
        raise ValueError("说明规则编号无效。")
    notes = {item["rule_id"]: item for item in supplied}
    groups = explanation_groups(records, fallback_type, rules)
    for group in groups:
        submitted = notes.get(group["rule_id"], {})
        note = submitted.get("note", group["note"])
        if not isinstance(note, str) or not note.strip() or len(note) > 10000:
            raise ValueError("说明文字不能为空或超过10000字。")
        if "【" in note or "请填写" in note:
            raise ValueError(f"请先在网页中填写《{group['title']}》的用途说明。")
        group.update(note=note.strip(), claimant=claimant, date=date)
    return groups
