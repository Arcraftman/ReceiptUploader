from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.receipts_ocr import OcrArtifact, _rule_candidates
from kdzwy_receipt_uploader.template_catalog import TemplateCatalog


TEMPLATE_ROOT = PROJECT / "templates" / "weiyu"


def _records_for(source: str) -> list[dict[str, object]]:
    catalog = TemplateCatalog.load(TEMPLATE_ROOT)
    records: list[dict[str, object]] = []
    for record in catalog.records:
        if not bool(record.get("enabled", True)):
            continue
        template = catalog.load_template(record)
        enriched = dict(record)
        enriched.update(
            {
                key: template[key]
                for key in (
                    "decisionCode",
                    "decisionName",
                    "documentBlock",
                    "documentType",
                    "settlementMethod",
                    "businessType",
                    "currency",
                    "matchRules",
                    "amountSource",
                )
                if key in template
            }
        )
        rules = enriched.get("matchRules") if isinstance(enriched.get("matchRules"), dict) else {}
        configured = {str(value).lower() for value in rules.get("sourceFolders", [])}
        physical = Path(str(enriched.get("path") or "")).parts[0].lower()
        if source in configured or (not configured and physical == source):
            records.append(enriched)
    return records


def _route(source: str, text: str) -> list[dict[str, object]]:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        metadata = root / "ocr.json"
        metadata.write_text(
            json.dumps(
                {
                    "fields": {
                        "allowedTemplateBlocks": {
                            "sales": ["销售"],
                            "purchase": ["采购", "费用"],
                            "bank": ["银行", "费用"],
                            "misc": ["杂项", "费用"],
                        }[source]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        artifact = OcrArtifact(
            invoice_code="demo",
            source_pdf=root / "demo.pdf",
            source_folder=source,
            source_side=source,
            output_dir=root,
            text_path=root / "ocr.txt",
            metadata_path=metadata,
            text=text,
            engine="fixture",
            status="success",
        )
        selected, _rejected = _rule_candidates(_records_for(source), artifact)
        return selected


def _assert_one(source: str, text: str, expected_id: str) -> None:
    selected = _route(source, text)
    assert [item["id"] for item in selected] == [expected_id]
    assert str(selected[0]["decisionCode"]).count(".") == 4


def test_weiyu_semantic_decision_codes_are_unique() -> None:
    catalog = TemplateCatalog.load(TEMPLATE_ROOT)
    codes: list[str] = []
    ids: list[str] = []
    paths: list[str] = []
    for record in catalog.records:
        if not bool(record.get("enabled", True)):
            continue
        template = catalog.load_template(record)
        assert template["id"] == record["id"]
        assert (TEMPLATE_ROOT / str(record["path"])).is_file()
        assert all(entry.get("dc") in {1, -1} for entry in template.get("entries", []))
        code = str(template["decisionCode"])
        assert code.count(".") == 4
        assert str(template["businessType"]) in code
        codes.append(code)
        ids.append(str(record["id"]))
        paths.append(str(record["path"]))
    assert len(codes) == len(set(codes))
    assert len(ids) == len(set(ids))
    assert len(paths) == len(set(paths))


def test_weiyu_kept_templates_use_their_own_source_and_verified_accounts() -> None:
    catalog = TemplateCatalog.load(TEMPLATE_ROOT)
    expected_sources = {"sales": "sales_map", "purchase": "purchase_map", "bank": "source", "misc": "source"}
    expected_accounts = {
        "expense-12": {"560107", "22210101", "2202"},
        "bank-06": {"22210101", "100201"},
        "bank-09": {"221102", "224108", "100201"},
        "bank-10": {"221103", "224109", "100201"},
        "bank-20": {"100201", "224105"},
        "bank-23": {"100204", "2001"},
    }
    counts = {source: 0 for source in expected_sources}
    for record in catalog.records:
        template = catalog.load_template(record)
        source = Path(str(record["path"])).parts[0]
        counts[source] += 1
        assert template["amountSource"] == expected_sources[source]
        if record["id"] in expected_accounts:
            accounts = {str(entry["accountSelector"]["number"]) for entry in template["entries"]}
            assert accounts == expected_accounts[str(record["id"])]
    assert counts == {"sales": 1, "purchase": 5, "bank": 12, "misc": 6}


def test_weiyu_ground_truth_manifest_covers_every_voucher() -> None:
    evidence = json.loads(
        (PROJECT / "docs" / "template_evidence" / "weiyu_2026-07-voucher-archetypes.json").read_text(encoding="utf-8")
    )
    family_total = sum(int(row["count"]) for row in evidence["routingFamilies"])
    assert evidence["statistics"] == {
        "entryRows": 1592,
        "vouchers": 584,
        "exactAccountSignatures": 343,
        "normalizedSummaryPatterns": 56,
    }
    assert family_total == evidence["statistics"]["vouchers"] == 584


def test_purchase_routes_specialized_services_before_inventory_default() -> None:
    _assert_one("purchase", "增值税电子发票 发票号码123 *云计算服务* 规格SaaS 数量1", "purchase-02")
    _assert_one("purchase", "数电发票 货物运输服务 物流费", "expense-12")
    _assert_one("purchase", "电子发票 直接收费金融服务 通讯手续费", "expense-20")
    _assert_one("purchase", "电子发票 电信服务 通讯费", "expense-14")
    _assert_one("purchase", "电子发票 物业服务 水费", "expense-15")
    assert _route("purchase", "电子发票 自用办公用品 打印纸") == []
    assert _route("purchase", "电子发票 固定资产 机器设备") == []
    assert _route("purchase", "电子发票 原材料 待认证进项税") == []
    assert _route("purchase", "Invoice USD 1000 采购商品") == []


def test_sales_and_misc_are_narrowed_to_one_template() -> None:
    _assert_one("sales", "电子发票 发票号码123 货物名称服务器 金额100 税额13", "sales-vat-settlement-income-cny")
    _assert_one("misc", "固定资产折旧 2026年7月", "misc-depreciation-accrual-cny")
    _assert_one("misc", "结转7月未交增值税", "misc-vat-carryforward-cny")


def test_bank_routes_real_voucher_archetypes_and_blocks_generic_transfer() -> None:
    _assert_one("bank", "招商银行电子回单 用途：付供应商款 货款", "bank-04")
    _assert_one("bank", "招商银行电子回单 摘要：收客户款", "bank-01")
    _assert_one("bank", "招商银行电子回单 代发工资", "bank-12")
    _assert_one("bank", "上海银行电子回单 缴增值税", "bank-06")
    _assert_one("bank", "上海银行电子回单 缴社保", "bank-09")
    _assert_one("bank", "上海银行电子回单 缴公积金", "bank-10")
    _assert_one("bank", "招商银行电子回单 股东借款", "bank-20")
    _assert_one("bank", "招商银行电子回单 贷款发放 流动资金贷款", "bank-23")
    assert _route("bank", "招商银行电子回单 银行账户管理费") == []
    _assert_one("bank", "上海银行电子回单 应付账款 发票款 转账手续费", "bank-13")
    _assert_one("bank", "缴增值税 城建税 教育费附加 地方教育费附加", "bank-tax-vat-surcharges-cny")
    assert _route("bank", "招商银行电子回单 转账") == []
    assert _route("bank", "上海银行电子回单 直接扣收转账手续费") == []
