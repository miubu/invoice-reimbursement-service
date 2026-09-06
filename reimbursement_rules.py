from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REIMBURSEMENT_TYPES = (
    "材料费",
    "书籍资料",
    "快递邮寄",
    "市内交通",
    "加工劳务",
    "加工劳务-对公",
    "对公转账",
    "其他",
)

DEFAULT_DB_PATH = Path(tempfile.gettempdir()) / "invoice-reimbursement-rules.db"
RULES_DB_PATH = Path(os.getenv("RULES_DB_PATH", str(DEFAULT_DB_PATH)))

DEFAULT_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "courier",
        "title": "快递邮寄",
        "description": "命中后自动将报销类型设为“快递邮寄”，并提示补充快递说明。",
        "reimbursement_type": "快递邮寄",
        "keywords": ["*物流辅助服务*物流服务费"],
        "remark": "请按照《快递报销说明模板》补充说明",
        "template_url": "http://10.7.135.67:8080/externalLinksController/downloadFileByKey/4%E5%BF%AB%E9%80%92%E6%8A%A5%E9%94%80%E8%AF%B4%E6%98%8E%E6%A8%A1%E6%9D%BF.docx?dkey=c00cfe23-dde9-41c7-aa0b-236b56063372",
        "enabled": True,
        "priority": 10,
    },
    {
        "id": "transport",
        "title": "市内交通",
        "description": "命中后自动将报销类型设为“市内交通”，并提示补充交通费说明。",
        "reimbursement_type": "市内交通",
        "keywords": ["*交通*"],
        "remark": "请按照《交通费说明模板》补充说明",
        "template_url": "http://10.7.135.67:8080/externalLinksController/downloadFileByKey/3%E4%BA%A4%E9%80%9A%E8%B4%B9%E8%AF%B4%E6%98%8E%E6%A8%A1%E6%9D%BF.docx?dkey=6caed1a8-bb42-4928-ab01-5ae60545f390",
        "enabled": True,
        "priority": 20,
    },
    {
        "id": "cable",
        "title": "线缆及连接配件",
        "description": "只增加连接线材说明，不改变报销类型。任意一个关键词命中即可。",
        "reimbursement_type": "",
        "keywords": ["线", "缆"],
        "remark": "请按照《连接线材与配电辅助配件说明模板》补充说明",
        "template_url": "http://10.7.135.67:8080/externalLinksController/downloadFileByKey/5%E8%BF%9E%E6%8E%A5%E7%BA%BF%E6%9D%90%E4%B8%8E%E9%85%8D%E7%94%B5%E8%BE%85%E5%8A%A9%E9%85%8D%E4%BB%B6%E8%AF%B4%E6%98%8E%E6%A8%A1%E6%9D%BF.docx?dkey=ca9dd1ed-5fac-476f-bcec-6b7ca458f1b5",
        "enabled": True,
        "priority": 30,
    },
    {
        "id": "computer_accessories",
        "title": "计算机配件限制",
        "description": "命中后标红提示“无法报销”，不改变报销类型。",
        "reimbursement_type": "",
        "keywords": ["计算机配件"],
        "remark": "无法报销",
        "template_url": "",
        "enabled": True,
        "priority": 40,
    },
)


@dataclass(frozen=True)
class ReimbursementRule:
    id: str
    title: str
    description: str
    reimbursement_type: str
    keywords: tuple[str, ...]
    remark: str
    template_url: str
    enabled: bool
    priority: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["keywords"] = list(self.keywords)
        return data


@dataclass(frozen=True)
class ClassificationResult:
    reimbursement_type: str
    remarks: tuple[str, ...]
    template_urls: tuple[str, ...]
    matched_rule_ids: tuple[str, ...]


def normalize_reimbursement_type(value: str) -> str:
    compact = re.sub(r"\s+", "", value or "")
    for option in REIMBURSEMENT_TYPES:
        if compact == re.sub(r"\s+", "", option):
            return option
    raise ValueError("报销类型必须从规定选项中选择。")


def _keyword_matches(keyword: str, compact_item: str) -> bool:
    compact_keyword = re.sub(r"\s+", "", keyword)
    if not compact_keyword:
        return False
    if "*" not in compact_keyword:
        return compact_keyword in compact_item
    wildcard_pattern = re.escape(compact_keyword).replace(r"\*", ".*")
    return re.search(wildcard_pattern, compact_item) is not None


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reimbursement_rules (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            reimbursement_type TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            remark TEXT NOT NULL,
            template_url TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            priority INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    for rule in DEFAULT_RULES:
        connection.execute(
            """
            INSERT OR IGNORE INTO reimbursement_rules
            (id, title, description, reimbursement_type, keywords_json, remark,
             template_url, enabled, priority, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule["id"],
                rule["title"],
                rule["description"],
                rule["reimbursement_type"],
                json.dumps(rule["keywords"], ensure_ascii=False),
                rule["remark"],
                rule["template_url"],
                int(rule["enabled"]),
                rule["priority"],
                now,
            ),
        )
    connection.commit()
    return connection


def get_rules(db_path: Path = RULES_DB_PATH) -> tuple[ReimbursementRule, ...]:
    with closing(_connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT * FROM reimbursement_rules ORDER BY priority, id"
        ).fetchall()
    return tuple(
        ReimbursementRule(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            reimbursement_type=row["reimbursement_type"],
            keywords=tuple(json.loads(row["keywords_json"])),
            remark=row["remark"],
            template_url=row["template_url"],
            enabled=bool(row["enabled"]),
            priority=int(row["priority"]),
        )
        for row in rows
    )


def update_rules(
    updates: list[dict[str, Any]],
    db_path: Path = RULES_DB_PATH,
) -> tuple[ReimbursementRule, ...]:
    allowed_ids = {rule["id"] for rule in DEFAULT_RULES}
    received_ids = {str(update.get("id", "")) for update in updates}
    if received_ids != allowed_ids or len(updates) != len(allowed_ids):
        raise ValueError("必须完整提交全部内置规则，且规则编号不能修改。")

    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for update in updates:
            rule_id = str(update["id"])
            reimbursement_type = str(update.get("reimbursement_type", "")).strip()
            if reimbursement_type:
                reimbursement_type = normalize_reimbursement_type(reimbursement_type)
            keywords = []
            seen: set[str] = set()
            for raw_keyword in update.get("keywords", []):
                keyword = str(raw_keyword).strip()
                if keyword and keyword not in seen:
                    keywords.append(keyword)
                    seen.add(keyword)
            if not keywords:
                raise ValueError(f"{rule_id} 至少需要一个关键词。")
            if len(keywords) > 30 or any(len(keyword) > 100 for keyword in keywords):
                raise ValueError(f"{rule_id} 的关键词数量或长度超过限制。")

            remark = str(update.get("remark", "")).strip()
            template_url = str(update.get("template_url", "")).strip()
            if len(remark) > 300:
                raise ValueError(f"{rule_id} 的备注不能超过 300 个字符。")
            if template_url and not template_url.startswith(("http://", "https://")):
                raise ValueError(f"{rule_id} 的模板链接必须以 http:// 或 https:// 开头。")
            if len(template_url) > 1000:
                raise ValueError(f"{rule_id} 的模板链接过长。")

            connection.execute(
                """
                UPDATE reimbursement_rules
                SET reimbursement_type = ?, keywords_json = ?, remark = ?,
                    template_url = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    reimbursement_type,
                    json.dumps(keywords, ensure_ascii=False),
                    remark,
                    template_url,
                    int(bool(update.get("enabled", True))),
                    now,
                    rule_id,
                ),
            )
        connection.commit()
    return get_rules(db_path)


def classify_reimbursement(
    item_name: str,
    fallback_type: str,
    rules: tuple[ReimbursementRule, ...] | None = None,
) -> ClassificationResult:
    reimbursement_type = normalize_reimbursement_type(fallback_type)
    compact_item = re.sub(r"\s+", "", item_name or "")
    remarks: list[str] = []
    template_urls: list[str] = []
    matched_rule_ids: list[str] = []
    type_was_assigned = False

    for rule in rules or get_rules():
        if not rule.enabled:
            continue
        matched = any(_keyword_matches(keyword, compact_item) for keyword in rule.keywords)
        if not matched:
            continue

        matched_rule_ids.append(rule.id)
        if rule.reimbursement_type and not type_was_assigned:
            reimbursement_type = normalize_reimbursement_type(rule.reimbursement_type)
            type_was_assigned = True
        if rule.remark or rule.template_url:
            remarks.append(rule.remark.strip() or "点击打开说明模板")
            template_urls.append(rule.template_url.strip())

    return ClassificationResult(
        reimbursement_type=reimbursement_type,
        remarks=tuple(remarks),
        template_urls=tuple(template_urls),
        matched_rule_ids=tuple(matched_rule_ids),
    )
