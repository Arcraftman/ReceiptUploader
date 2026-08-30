from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import company_config_filename
from kdzwy_receipt_uploader.source_profile import BUILT_IN_SOURCES


SOURCES = BUILT_IN_SOURCES


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取 JSON：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"JSON 顶层必须是对象：{path}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据账套公司名称创建一套跨期间复用的标准模板")
    parser.add_argument("--name", required=True, help="公司完整中文名称")
    parser.add_argument("--base-template", default="weiyu", help="明确指定复制来源模板 key；默认 weiyu")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    company_name = args.name.strip()
    if not company_name:
        raise SystemExit("--name不能为空")
    base_template_key = args.base_template.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", base_template_key):
        raise SystemExit(f"--base-template格式不正确：{base_template_key}")

    accountbooks_path = ROOT / "runtime" / "registry" / "accountbooks.json"
    accountbooks_payload = read_json_object(accountbooks_path)
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
    company_id = str(matches[0].get("company_id") or "").strip()
    if not company_id:
        raise SystemExit(f"账套缺少company_id：{company_name}；请重新运行公司发现流程")
    company_key = f"company_{company_id.lower()}"
    if str(matches[0].get("key") or "").strip().lower() != company_key:
        raise SystemExit(f"账套key不符合统一规则：应为 {company_key}；请重新运行公司发现流程")

    target_template_dir = ROOT / "templates" / company_key
    company_config_path = ROOT / "config" / "companies" / company_config_filename(company_id, company_name)
    registry_path = ROOT / "config" / "template_companies.json"
    if company_config_path.exists():
        raise SystemExit(f"公司配置已存在，未覆盖：{company_config_path}")
    registry = read_json_object(registry_path)
    records = registry.get("template_companies")
    if not isinstance(records, list):
        raise SystemExit(f"模板公司注册表格式错误：{registry_path}")
    base_matches = [
        record
        for record in records
        if isinstance(record, dict)
        and str(record.get("key") or "").strip().lower() == base_template_key
        and bool(record.get("enabled", True))
    ]
    if len(base_matches) != 1:
        available = ", ".join(
            sorted(
                str(record.get("key"))
                for record in records
                if isinstance(record, dict) and bool(record.get("enabled", True)) and record.get("key")
            )
        )
        raise SystemExit(f"基础模板不存在或未启用：{base_template_key}；可用模板：{available or '无'}")
    source_directory = str(base_matches[0].get("directory") or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", source_directory):
        raise SystemExit(f"基础模板目录格式不正确：{source_directory}")
    source_template_dir = ROOT / "templates" / source_directory
    if not source_template_dir.is_dir():
        raise SystemExit(f"模板来源不存在：{source_template_dir}")
    if target_template_dir.exists():
        raise SystemExit(f"目标模板目录已存在，未覆盖：{target_template_dir}")

    if any(str(record.get("key")) == company_key for record in records if isinstance(record, dict)):
        raise SystemExit(f"模板公司已经登记，未修改：{company_key}")

    shutil.copytree(source_template_dir, target_template_dir)
    for source in SOURCES:
        (target_template_dir / source).mkdir(parents=True, exist_ok=True)

    index_path = target_template_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8-sig"))
    index["description"] = f"{company_name}业务模板；基础模板={base_template_key}，与目标账套动态目录独立。"
    write_json(index_path, index)

    company_config = {
        "version": 3,
        "company_key": company_key,
        "company_id": company_id,
        "company_name": company_name,
        "template_company": company_key,
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
        "base_template": base_template_key,
        "template_directory": str(target_template_dir),
        "next": "运行 commands/initialize_month.bat COMPANY_CONFIG_NAME YYYY-MM；四类资料目录会固定创建。",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
