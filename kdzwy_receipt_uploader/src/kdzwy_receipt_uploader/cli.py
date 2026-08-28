from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api import KdzwyApi
from .config import AppConfig
from .map_lookup import InvoicePdfMap
from .models import ApiError, ReceiptError
from .paths import ProjectPaths
from .workflow import find_receipts, load_snapshot, preview, process_one
from .responsibility_chain import run_selected_sources_safe
from .source_profile import source_from_folder_name, normalize_source_key
from .simple_logging import configure_pipeline_logger


def audit(paths: ProjectPaths, result: dict[str, Any]) -> None:
    with (paths.logs / "run.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")


def receipt_source(path: Path, receipt: Any) -> str:
    """Resolve source from 语义化目录（sales/purchase/bank/misc）或 OCR 元数据。"""
    for part in reversed(path.parts):
        lower = part.lower()
        if lower in {"receipts_sales", "receipts_salesinvoice", "receipts_sales_invoice", "receipts_销售", "receipts_销售发票"}:
            return "sales"
        if lower in {"receipts_purchase", "receipts_purchaseinvoice", "receipts_purchase_invoice", "receipts_进项", "receipts_进项发票"}:
            return "purchase"
        if lower in {"receipts_bank", "receipts_bank_receipts", "receipts_银行", "receipts_银行回单", "receipts_银行流水"}:
            return "bank"
        if lower in {"receipts_misc", "receipts_miscellaneous", "receipts_杂项", "receipts_其他"}:
            return "misc"
        inferred = source_from_folder_name(part)
        if inferred:
            return inferred
    source = str(getattr(receipt, "source", "") or "").lower()
    if source in {"sales", "purchase", "bank", "misc"}:
        return source
    analysis = receipt.voucher.get("ocrAnalysis", {}) if isinstance(receipt.voucher, dict) else {}
    folder = source_from_folder_name(str(analysis.get("sourceFolder", "")))
    return folder or ""


def select_random_receipts(valid: list[tuple[Path, Any]], source: str, count: int, seed: int | None = None) -> list[tuple[Path, Any]]:
    """Select deterministic random samples by source folder for test uploads."""
    if count < 1:
        raise ValueError("--random-count 必须是正整数")
    source_prefixes = [source] if source != "all" else ["sales", "purchase", "bank", "misc"]
    candidates = [(path, receipt) for path, receipt in valid if receipt_source(path, receipt) in source_prefixes]
    if not candidates:
        return []
    rng = random.Random(seed)
    rng.shuffle(candidates)
    return candidates[:min(count, len(candidates))]


def run_confirm_sequential(
    valid: list[tuple[Path, Any]],
    api: KdzwyApi,
    paths: ProjectPaths,
) -> tuple[bool, int]:
    """Process receipts in order; stop only on an API/workflow failure."""
    completed = 0
    for index, (path, receipt) in enumerate(valid, start=1):
        try:
            print(f"提交第 {index}/{len(valid)} 张：{receipt.receipt_id}；上一张未成功不会进入本张")
            result = process_one(receipt, api)
            result["unresolvedInvoiceCodes"] = receipt.unresolved_invoice_codes
            audit(paths, result)
            try:
                _write_upload_checkpoint(path, result)
            except OSError as checkpoint_error:
                print(f"上传成功且审计记录已保存，但 receipt 状态回写暂缓：{receipt.receipt_id} -> {checkpoint_error}", file=sys.stderr)
            completed += 1
            print(f"提交并回查成功：{receipt.receipt_id} -> {result['voucherNo']} / {result['voucherId']}")
            if index < len(valid):
                print("等待 3 秒后提交下一张，降低连续请求压力...")
                time.sleep(3.0)
        except (ApiError, ReceiptError, ValueError, OSError) as exc:
            result = {
                "status": "failed_or_ambiguous",
                "receiptId": receipt.receipt_id,
                "file": str(path),
                "error": str(exc),
                "completedAt": datetime.now(timezone.utc).isoformat(),
                "stoppedBeforeNext": True,
            }
            audit(paths, result)
            try:
                archive(path, paths.failed, receipt, result)
            except OSError as archive_error:
                audit(paths, {**result, "archiveStatus": "failed", "archiveError": str(archive_error)})
            print(f"处理失败，已停止后续上传且不自动重试：{receipt.receipt_id} -> {exc}", file=sys.stderr)
            return True, completed
    return False, completed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="账无忧凭证与 PDF 附件批处理")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--runtime-root", type=Path, default=None, help="本次账套工作区；日志、失败归档和审计均写入该目录")
    parser.add_argument("--input-dir", type=Path, default=None, help="receipt 输入目录，默认 data/inbox")
    parser.add_argument("--config", type=Path, default=None, help="配置 JSON，默认 config/app.json")
    parser.add_argument("--expected-company", type=str, default="", help="强制会话公司名与目标公司完全一致")
    parser.add_argument("--confirm", action="store_true", help="确认后真实保存凭证并上传 PDF")
    parser.add_argument(
        "--source",
        choices=["sales", "purchase", "bank", "misc", "all"],
        default="all",
        help="责任链来源：sales 销售, purchase 进项, bank 银行, misc 杂项, all 全部",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--receipt-id", type=str, default="", help="只处理一个指定 receiptId，用于首次单张验证")
    parser.add_argument("--test-upload", action="store_true", help="测试上传模式：按来源随机选择少量 receipt")
    parser.add_argument("--random-count", type=int, default=1, help="测试上传随机选择数量，默认1")
    parser.add_argument("--random-seed", type=int, default=None, help="测试上传随机种子，便于复现样本")
    parser.add_argument("--pdf-map", type=Path, default=None, help="PDF映射文件；sales/purchase测试默认优先使用 upload_pdf_map.json")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    runtime_root = args.runtime_root or (root / "workspaces" / "_manual")
    if runtime_root is not None and not runtime_root.is_absolute():
        runtime_root = root / runtime_root
    paths = ProjectPaths.from_root(root, runtime_root)
    paths.ensure()
    logger = configure_pipeline_logger(paths.logs, "batch_receipts")
    input_dir = args.input_dir or paths.inbox
    if not input_dir.is_absolute():
        input_dir = root / input_dir
    input_dir = input_dir.resolve()
    config_path = args.config or paths.config / "app.json"
    if not config_path.is_absolute():
        config_path = root / config_path
    config = AppConfig.from_json(config_path, root)
    if args.expected_company:
        config = replace(config, expected_company=args.expected_company.strip())
    pdf_map = None
    pdf_map_path = args.pdf_map
    source = normalize_source_key(args.source) or "sales"
    if pdf_map_path is None:
        map_names = ["upload_pdf_map.json", "xlsx_pdf_map.json"] if args.test_upload and source in {"sales", "purchase"} else ["xlsx_pdf_map.json"]
        for map_name in map_names:
            candidate = input_dir.parent / "maps" / map_name if input_dir.name.startswith("receipts_") else input_dir / "maps" / map_name
            if candidate.is_file():
                pdf_map_path = candidate
                break
    if pdf_map_path:
        pdf_map_path = pdf_map_path if pdf_map_path.is_absolute() else root / pdf_map_path
        pdf_map = InvoicePdfMap.load(pdf_map_path.resolve())
        print(f"PDF映射：{pdf_map_path.resolve()}")
    try:
        snapshot = load_snapshot(config.account_snapshot)
        valid, invalid = find_receipts(input_dir, snapshot, pdf_map)
    except ReceiptError as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 2
    print(f"项目根目录：{root}")
    print(f"输入目录：{input_dir}")
    logger.info("batch start: project=%s input=%s source=%s confirm=%s limit=%s", root, input_dir, source, args.confirm, args.limit)
    chain_contexts = run_selected_sources_safe(input_dir, source, "confirm" if args.confirm else "dry-run")
    blocked_sources = [item.source.value for item in chain_contexts if item.data.get("blocked")]
    if args.receipt_id:
        valid = [(path, receipt) for path, receipt in valid if receipt.receipt_id == args.receipt_id]
        if not valid:
            print(f"未找到指定 receiptId：{args.receipt_id}", file=sys.stderr)
            return 2
    if args.test_upload:
        if source in {"bank", "misc", "all"}:
            print("测试上传已阻断：bank/misc 业务规则尚未完成；请先使用 --source sales 或 --source purchase。", file=sys.stderr)
            return 3
        valid = select_random_receipts(valid, source, args.random_count, args.random_seed)
        print(f"测试上传样本：{[receipt.receipt_id for _, receipt in valid]}，seed={args.random_seed}")
        if not valid:
            print("指定来源下没有可用 receipt。", file=sys.stderr)
            return 2
    print(f"责任链来源：{source}（sales销项/purchase进项/bank银行/misc杂项/all全部）")
    print(f"责任链步骤：{[(item.source.value, item.steps) for item in chain_contexts]}")
    if blocked_sources:
        print(f"责任链阻断来源：{','.join(blocked_sources)}；不会误上传。")
    print(f"有效 receipt：{len(valid)}，无效文件：{len(invalid)}，模式：{'CONFIRM' if args.confirm else 'DRY-RUN'}")
    for item in invalid:
        result = {"status": "validation_failed", **item, "completedAt": datetime.now(timezone.utc).isoformat()}
        print(f"校验失败：{item['file']} -> {item['error']}")
        audit(paths, result)
    if args.confirm and source in {"bank", "misc", "all"}:
        print("真实上传已阻断：bank/misc/all 责任链尚未完成业务规则封装；请先使用 --source sales 或 --source purchase。", file=sys.stderr)
        logger.error("阻断：bank/misc/all 不允许真实上传")
        return 3
    if args.limit > 0:
        logger.info("应用 limit=%s", args.limit)
        valid = valid[: args.limit]
    if not valid:
        print("没有可处理的有效 receipt。")
        return 1 if invalid else 0
    if not args.confirm:
        for path, receipt in valid:
            number_preview = {"vchNum": "运行时获取", "year": "运行时获取", "period": "运行时获取"}
            print(f"预览 {path.name}：{json.dumps({'receiptId': receipt.receipt_id, 'number': number_preview, 'attachmentFiles': [{'path': x.relative_path, 'size': x.size} for x in receipt.attachment_files], 'entryIds': [e.get('entryId') for e in receipt.voucher['entries']]}, ensure_ascii=False)}")
        print("DRY-RUN 完成：未调用任何真实接口。")
        logger.info("DRY-RUN 完成: valid=%s invalid=%s", len(valid), len(invalid))
        return 1 if invalid else 0
    try:
        api = KdzwyApi(config)
    except ApiError as exc:
        print(f"无法加载账簿会话：{exc}", file=sys.stderr)
        return 2
    try:
        failed, processed = run_confirm_sequential(valid, api, paths)
    finally:
        if hasattr(api, "close"):
            api.close()
    if failed:
        print(f"批次已停止：本批成功 {processed} 张，下一张失败，后续未执行。", file=sys.stderr)
        logger.error("批次中断: 已处理=%s", processed)
    else:
        print(f"批次完成：串行成功处理 {processed} 张。")
        logger.info("批次完成: processed=%s", processed)
    return 1 if (failed or invalid) else 0


if __name__ == "__main__":
    raise SystemExit(main())
