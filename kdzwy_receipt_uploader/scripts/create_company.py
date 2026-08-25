from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("sales", "purchase", "bank", "misc")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据账套公司名称创建一套跨期间复用的标准模板")
    parser.add_argument("--name", required=True, help="公司完整中文名称")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    company_name = args.name.strip()
    if not company_name:
        raise SystemExit("--name不能为空")

    accountbooks_path = ROOT / "config" / "accountbooks.json"
    accountbooks_payload = json.loads(accountbooks_path.read_text(encoding="utf-8-sig"))
    accountbooks = accountbooks_payload.get("accountbooks")
    if not isinstance(accountbooks, list):
        raise SystemExit(f"账套配置格式错误：{accountbooks_path}")
    matches = [
        record for record in accountbooks
        if isinstance(record, dict)
        and str(record.get("company_name") or record.get("companyName") or record.get("name") or "").strip() == company_name
    ]
    if len(matches) != 1:
        raise SystemExit(f"无法按公司全名唯一找到账套：name={company_name}, matches={len(matches)}；请先运行公司发现和登录流程")
    company_key = str(matches[0].get("key") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", company_key):
        raise SystemExit(f"账套company_key格式不正确：{company_key}")
    dataset = company_key

    source_template_dir = ROOT / "templates" / "weiyu"
    target_template_dir = ROOT / "templates" / company_key
    company_config_path = ROOT / "config" / "companies" / f"{company_key}.json"
    registry_path = ROOT / "config" / "template_companies.json"
    if not source_template_dir.is_dir():
        raise SystemExit(f"模板来源不存在：{source_template_dir}")
    if target_template_dir.exists():
        raise SystemExit(f"目标模板目录已存在，未覆盖：{target_template_dir}")
    if company_config_path.exists():
        raise SystemExit(f"公司配置已存在，未覆盖：{company_config_path}")

    registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    records = registry.get("template_companies")
    if not isinstance(records, list):
        raise SystemExit(f"模板公司注册表格式错误：{registry_path}")
    if any(str(record.get("key")) == company_key for record in records if isinstance(record, dict)):
        raise SystemExit(f"模板公司已经登记，未修改：{company_key}")

    shutil.copytree(source_template_dir, target_template_dir)
    for source in SOURCES:
        (target_template_dir / source).mkdir(parents=True, exist_ok=True)
    prompt_source = source_template_dir / "prompts" / "deepseek_invoice_classifier_prompt.txt"
    prompt_target = target_template_dir / "prompts" / "deepseek_invoice_classifier_prompt.txt"
    prompt_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prompt_source, prompt_target)

    index_path = target_template_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8-sig"))
    index["description"] = f"{company_name}业务模板；与目标账套动态目录独立。"
    write_json(index_path, index)

    company_config = {
        "version": 1,
        "company_key": company_key,
        "enabled": True,
        "dataset": dataset,
        "template_company": company_key,
        "month": "",
        "defaults": {
            "mode": "analysis-only",
            "analysis_stage": "ocr",
            "analysis_validation": "strict",
            "preload_items": "once",
            "purpose": "test",
            "allow_cross_entity": False,
        },
        "sources": {source: {"enabled": False} for source in SOURCES},
    }
    write_json(company_config_path, company_config)

    records.append({
        "key": company_key,
        "name": company_name,
        "directory": company_key,
        "enabled": True,
    })
    write_json(registry_path, registry)

    print(json.dumps({
        "status": "ok",
        "company_key": company_key,
        "company_name": company_name,
        "company_config": str(company_config_path),
        "template_directory": str(target_template_dir),
        "next": "在公司配置中填写dataset和month，并启用需要处理的sources；同一模板目录可跨期间复用。",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
