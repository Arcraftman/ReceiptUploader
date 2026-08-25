"""Initialize or refresh a template workspace from a configured live account book."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.company_registry import load_accountbooks, validate_accountbook_session
from kdzwy_receipt_uploader.config import AppConfig
from kdzwy_receipt_uploader.fixed_prompt_rules import FIXED_DEEPSEEK_RULES

SOURCES = {
    "sales": "销项发票：dataset 公司必须是销售方 seller，交易对方必须是购买方 buyer。",
    "purchase": "进项发票及费用：dataset 公司必须是购买方 buyer，交易对方必须是销售方 seller。",
    "bank": "银行业务：按银行账户子目录分别处理；必须识别收付款方向和交易对方。",
    "misc": "杂项业务：只能使用 misc 目录中的模板；信息不足时返回 blocked。",
}
STANDARD_ITEM_CLASSES = {1: "客户", 2: "职员", 3: "项目", 4: "存货", 5: "供应商", 6: "部门"}
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def flatten(rows: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            result.append(row)
            result.extend(flatten(row.get("child", [])))
    return result


def resolve_class_id(row: dict[str, object]) -> int | None:
    for key in ("id", "itemClassId", "classId"):
        try:
            value = int(str(row.get(key) or ""))
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def fetch_all_items(api: KdzwyApi, classes: list[dict[str, object]], page_size: int = 500) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item_class in classes:
        item_class_id = resolve_class_id(item_class)
        if item_class_id is None:
            continue
        page, items = 1, []
        while True:
            payload = api.get_items_v1(item_class_id, page=page, page_size=page_size)
            items.extend(row for row in payload.get("rows", []) if isinstance(row, dict))
            if page >= int(payload.get("totalPage") or 1):
                break
            page += 1
        result.append({"itemClass": item_class, "itemClassId": item_class_id, "count": len(items), "items": items})
    return result


def update_registry(key: str, name: str) -> None:
    path = ROOT / "config" / "template_companies.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.setdefault("template_companies", [])
    record = next((row for row in records if isinstance(row, dict) and row.get("key") == key), None)
    changed = False
    if record is None:
        records.append({"key": key, "name": name, "directory": key, "enabled": True})
        changed = True
    else:
        expected = {"name": name, "directory": key, "enabled": True}
        changed = any(record.get(field) != value for field, value in expected.items())
        if changed:
            record.update(expected)
    if changed:
        # This configuration can be protected against atomic replacement by
        # Windows ACLs even though ordinary content updates are allowed.
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="从任意已配置账套动态生成公司模板工作区")
    parser.add_argument("--accountbook", required=True, help="账套英文 key")
    parser.add_argument("--company-template", required=True, help="模板目录英文 key")
    parser.add_argument("--company-name", default="", help="模板公司名称，默认使用账套名称")
    parser.add_argument("--overwrite-prompts", action="store_true", help="覆盖已有公司自定义提示词")
    args = parser.parse_args()
    if not KEY_PATTERN.fullmatch(args.company_template):
        raise SystemExit("company-template 必须英文小写开头，且只能包含英文小写、数字、下划线或连字符")

    accountbook = load_accountbooks(ROOT / "config" / "accountbooks.json").get(args.accountbook)
    if accountbook is None or not accountbook.enabled:
        raise SystemExit(f"账套不存在或未启用：{args.accountbook}")
    company_name = args.company_name.strip() or accountbook.name
    session = validate_accountbook_session(ROOT, accountbook)
    api = KdzwyApi(replace(AppConfig.from_json(ROOT / "config" / "app.json", ROOT), cookie_file=session, expected_company=accountbook.name))
    params = api.get_dynamic_system_params()
    accounts = flatten(api.get_subject_tree(effective=0, expand=True).get("rows", []))
    item_classes = [row for row in api.get_item_classes(show_collection=True) if isinstance(row, dict)]
    if not item_classes:
        item_classes = [{"id": class_id, "name": name, "source": "standard-fallback"} for class_id, name in STANDARD_ITEM_CLASSES.items()]
    all_items = fetch_all_items(api, item_classes)

    company_root = ROOT / "templates" / args.company_template
    prompts, catalog = company_root / "prompts", company_root / "catalog"
    for source in SOURCES:
        (company_root / source).mkdir(parents=True, exist_ok=True)
    prompts.mkdir(parents=True, exist_ok=True)
    catalog.mkdir(parents=True, exist_ok=True)
    (prompts / "_fixed_rules.md").write_text(FIXED_DEEPSEEK_RULES.rstrip() + "\n", encoding="utf-8")
    for source, rule in SOURCES.items():
        path = prompts / f"{source}.md"
        if args.overwrite_prompts or not path.exists():
            path.write_text(f"# {company_name} / {source} 自定义规则\n\n{rule}\n只能选择 {{template_directory}} 下当前业务目录中的模板。\n\n## 用户补充规则\n\n", encoding="utf-8")

    generated_at = datetime.now(timezone.utc).isoformat()
    atomic_json(catalog / "accounts.json", {"company": company_name, "accountbook": args.accountbook, "period": params.get("CURPERIOD"), "generatedAt": generated_at, "count": len(accounts), "accounts": accounts})
    atomic_json(catalog / "auxiliary_items.json", {"company": company_name, "accountbook": args.accountbook, "generatedAt": generated_at, "itemClassCount": len(item_classes), "itemClasses": all_items})
    index_path = company_root / "index.json"
    if not index_path.exists():
        atomic_json(index_path, {"version": "3.0", "layout": "company/source-block", "templatePattern": "*_template.json", "description": f"{company_name}业务模板", "templates": []})
    item_count = sum(int(row["count"]) for row in all_items)
    atomic_json(company_root / "workspace.json", {"templateCompany": args.company_template, "companyName": company_name, "accountbook": args.accountbook, "period": params.get("CURPERIOD"), "generatedAt": generated_at, "accountCount": len(accounts), "itemClassCount": len(item_classes), "itemCount": item_count, "sources": list(SOURCES)})
    update_registry(args.company_template, company_name)
    print(f"模板工作区已生成：{company_root}")
    print(f"动态科目：{len(accounts)}；辅助核算类别：{len(item_classes)}；辅助项目：{item_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
