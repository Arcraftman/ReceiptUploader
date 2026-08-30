"""List the authoritative special-object bank filter for one company month."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    dataset_from_company,
    load_accountbooks,
    load_company_jobs,
    load_company_profile,
    normalize_month,
    resolve_project_path,
    resolve_target_accountbook,
    workspace_relative_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="列出已从普通流程分离的银行 exception")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--month", required=True)
    args = parser.parse_args()
    try:
        company = load_company_profile(args.config.resolve())
        dataset = dataset_from_company(company)
        month = normalize_month(args.month)
        project_path = resolve_project_path(
            ROOT, f"{dataset.data_root}/{month}/project.json"
        )
        jobs = load_company_jobs(project_path, company)
        bank_job = next(job for job in jobs if job.source == "bank")
        accountbooks = load_accountbooks(
            ROOT / "runtime" / "registry" / "accountbooks.json"
        )
        accountbook = resolve_target_accountbook(bank_job, accountbooks)
        workspace = ROOT / workspace_relative_path(
            accountbook.login_account or "default",
            accountbook.key,
            dataset.key,
            month,
        )
        exception_path = (
            workspace / "generated" / "maps" / "bank" / "bank_exceptions.json"
        )
        exception_map = json.loads(exception_path.read_text(encoding="utf-8-sig"))
    except (
        CompanyRegistryError,
        OSError,
        json.JSONDecodeError,
        StopIteration,
    ) as exc:
        print(f"无法读取银行 exception：{exc}", file=sys.stderr)
        return 2

    entries = exception_map.get("entries")
    if not isinstance(entries, dict):
        print(f"银行 exception 清单格式错误：{exception_path}", file=sys.stderr)
        return 2
    summary = exception_map.get("summary") if isinstance(exception_map.get("summary"), dict) else {}

    print("[银行 exception] 特殊对象已从普通流程分离；原始裁剪 PDF 保留")
    print(f"  资料公司：{company.name}")
    print(f"  目标账套：{accountbook.name}")
    print(f"  会计月份：{month}")
    print(
        f"  配置名称：{summary.get('exceptionNameCount', '-')}；"
        f"名单流水：{summary.get('exceptionStatementCount', len(entries))}；"
        f"切割exception：{summary.get('splitExceptionPdfCount', '-')}；"
        f"特殊PDF={summary.get('exceptionPdfCount', '-')}；"
        f"已复制={summary.get('copiedPdfCount', '-')}；"
        f"缺少PDF={summary.get('missingPdfCount', '-')}"
    )
    for key, item in sorted(entries.items()):
        if not isinstance(item, dict):
            continue
        print(
            f"  - {key} | {item.get('counterpartyName') or '-'} | "
            f"金额={item.get('amount') or '-'} | PDF={item.get('pdfStatus') or '-'} | "
            f"识别={item.get('matchMethod') or '-'}"
        )
        if item.get("copiedPdf"):
            print(f"    特殊副本：{item['copiedPdf']}")
        if item.get("sourcePdf"):
            print(f"    裁剪原件：{item['sourcePdf']}")
    if not entries:
        print("[完成] 当前没有配置或命中的银行特殊对象。")
    print(f"  清单文件：{exception_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
