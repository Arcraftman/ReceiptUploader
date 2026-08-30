"""Resolve one configured month and show bank receipt draft readiness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.bank_receipt_verifier import verify_bank_receipts  # noqa: E402
from kdzwy_receipt_uploader.bank_final_receipts import (  # noqa: E402
    BankFinalReceiptError,
    load_bank_records,
)
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
    parser = argparse.ArgumentParser(description="显示银行 receipt 的 draft 和提交前校验状态")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--month", required=True)
    args = parser.parse_args()
    try:
        company = load_company_profile(args.config.resolve())
        dataset = dataset_from_company(company)
        month = normalize_month(args.month)
        project_path = resolve_project_path(ROOT, f"{dataset.data_root}/{month}/project.json")
        jobs = load_company_jobs(project_path, company)
        bank_jobs = [job for job in jobs if job.source == "bank"]
        if len(bank_jobs) != 1:
            raise CompanyRegistryError(f"月份配置中必须恰好有一个 bank 业务：{project_path}")
        analysis_stage = str(bank_jobs[0].overrides.get("analysis_stage") or "")
        if analysis_stage != "existing":
            print(
                "[未到验证阶段] bank 必须先完成 LLM 分析，再设置 "
                'mode=prepare、analysis_stage=existing 生成最终 receipt。'
            )
            return 1
        accountbooks = load_accountbooks(ROOT / "runtime" / "registry" / "accountbooks.json")
        accountbook = resolve_target_accountbook(bank_jobs[0], accountbooks)
        workspace = ROOT / workspace_relative_path(
            accountbook.login_account or "default",
            accountbook.key,
            dataset.key,
            month,
        )
        receipt_root = workspace / "generated" / "receipts" / "bank"
        bank_map_root = workspace / "generated" / "maps" / "bank"
        matched_records, _ = load_bank_records(
            bank_map_root / "bank_map.json",
            bank_map_root / "bank_map.report.json",
        )
        report = verify_bank_receipts(
            receipt_root, allowed_record_keys=set(matched_records)
        )
    except (
        BankFinalReceiptError,
        CompanyRegistryError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"银行 receipt 验证失败：{exc}", file=sys.stderr)
        return 2

    summary = report["summary"]
    print("[验证] 银行 receipt")
    print(f"  资料公司：{company.name}")
    print(f"  目标账套：{accountbook.name}")
    print(f"  会计月份：{month}")
    print(
        f"  总数：{summary['receiptCount']}；draft=true：{summary['draftCount']}；"
        f"可提交：{summary['readyCount']}；无效：{summary['invalidCount']}；"
        f"旧/特殊产物：{summary.get('orphanCount', 0)}"
    )
    if report["drafts"]:
        print("[待人工填写] 以下 receipt 仍为 draft=true：")
        for item in report["drafts"]:
            print(f"  - 号码：{item['statementIndex'] or item['receiptId']}")
            print(f"    receiptId：{item['receiptId']}")
            print(f"    receipt.json：{item['receipt']}")
    if report["invalid"]:
        print("[未通过] 以下 receipt 已是 draft=false，但字段校验失败：")
        for item in report["invalid"]:
            print(f"  - {item.get('receiptId') or item['receipt']}：{item['error']}")
            print(f"    {item['receipt']}")
    print(f"  验证报告：{receipt_root / 'bank_receipts.verify.report.json'}")
    if report["status"] == "ready":
        print("[通过] 所有银行 receipt 均为 draft=false，且字段校验通过。")
        return 0
    if report["status"] == "empty":
        print("[未通过] 尚未生成最终银行 receipt；请先完成 LLM，再运行 prepare + existing。")
        return 1
    print("[未通过] 请补齐上述 receipt；完成后手动将 draft 改为 false，再次运行 verify。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
