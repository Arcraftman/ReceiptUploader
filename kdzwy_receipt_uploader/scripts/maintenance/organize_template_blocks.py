"""Move company templates into sales/purchase/bank/misc detail directories."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCK_DIRECTORY = {"销售": "sales", "采购": "purchase", "费用": "purchase", "银行": "bank", "杂项": "misc"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-template", action="append", required=True)
    args = parser.parse_args()
    for company in args.company_template:
        root = (ROOT / "templates" / company).resolve()
        root.relative_to((ROOT / "templates").resolve())
        index_path = root / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        records = {str(item.get("path")): item for item in index.get("templates", []) if isinstance(item, dict)}
        moved = 0
        for source in sorted(root.glob("*_template.json")):
            payload = json.loads(source.read_text(encoding="utf-8"))
            directory = BLOCK_DIRECTORY.get(str(payload.get("documentBlock") or ""), "misc")
            target_dir = root / directory
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name
            if target.exists():
                raise RuntimeError(f"目标模板已存在：{target}")
            source.replace(target)
            old_record = records.pop(source.name, None)
            if old_record is not None:
                old_record["path"] = f"{directory}/{source.name}"
                records[old_record["path"]] = old_record
            moved += 1
        index["layout"] = "company/source-block"
        index["templatePattern"] = "*_template.json"
        index["templates"] = list(records.values())
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{company}: moved={moved}, indexed={len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
