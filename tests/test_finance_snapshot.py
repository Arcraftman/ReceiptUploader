from decimal import Decimal
from pathlib import Path
import json
from xml.etree import ElementTree as ET

import pytest

from kdzwy_receipt_uploader.finance.snapshot import normalize_entries, spreadsheet_xml, number, MANAGED_SHEETS
from kdzwy_receipt_uploader.read_api_checks import CheckFailure, validate_request


def voucher(entries):
    return {"id": "123456789012345678", "date": "2026-08-01", "voucherNo": "记-1",
            "entries": entries, "debitTotal": "10", "creditTotal": "10"}


def entry(account, dc, amount="10"):
    return {"accountName": account, "dc": dc, "amount": amount, "amountFor": amount, "cur": "RMB"}


def test_cash_transfer_and_external_cash_are_distinct():
    rows = normalize_entries([voucher([entry("100201 银行存款", 1), entry("100202 银行存款", -1)])])
    assert all(r[-1] == "内部划转" for r in rows)
    rows = normalize_entries([voucher([entry("100201 银行存款", 1), entry("112201_004 应收账款_客户", -1)])])
    assert rows[1][4] == "112201"
    assert rows[0][-1] == "其他收支"
    assert rows[0][0] == "123456789012345678"


def test_unbalanced_and_nonfinite_amounts_stop_refresh():
    with pytest.raises(CheckFailure):
        normalize_entries([voucher([entry("100201 银行存款", 1), entry("1122 应收", -1, "9")])])
    with pytest.raises(CheckFailure):
        number("NaN")
    assert number("") is None


def test_xml_preserves_identifier_and_literal_formula():
    body = spreadsheet_xml({"sheets": {"凭证明细": [["123456789012345678", "=1+1", 12.3, None]]}})
    root = ET.fromstring(body)
    ns = "urn:schemas-microsoft-com:office:spreadsheet"
    cells = root.findall(f".//{{{ns}}}Data")
    assert cells[0].attrib[f"{{{ns}}}Type"] == "String"
    assert cells[0].text == "123456789012345678"
    assert cells[1].text == "=1+1"
    assert cells[2].attrib[f"{{{ns}}}Type"] == "Number"
    assert b"Formula=" not in body
    with pytest.raises(CheckFailure):
        spreadsheet_xml({"sheets": {"预算输入": [[1]]}})


def test_sources_all_pass_independent_read_allowlist():
    sources = json.loads((Path(__file__).parents[1] / "config/finance_read_sources.json").read_text(encoding="utf-8"))
    for case in sources.values():
        validate_request(case["method"], case["path"].replace("{dbId}", "123"), case.get("query", {}), case.get("body"), "123")


def test_excel_refresh_only_writes_managed_sheets_and_stages_before_write():
    vba = (Path(__file__).parents[1] / "excel/FinanceRefresh.bas").read_text(encoding="utf-8")
    line = next(line for line in vba.splitlines() if 'names = Array(' in line)
    assert all('"'+name+'"' in line for name in MANAGED_SHEETS)
    assert '"预算输入"' not in line and '"账龄输入"' not in line
    assert vba.index('incoming(i) = values') < vba.index('written = True')
    assert 'oldValues(i) = ws.UsedRange.Value2' in vba
    assert '"\'" & values(r, c)' in vba


@pytest.mark.parametrize('selected_month', [2, 12])
def test_snapshot_removes_auxiliary_totals_and_adds_nonauxiliary_accounts(tmp_path, selected_month):
    from kdzwy_receipt_uploader.finance.snapshot import collect_snapshot
    root = Path(__file__).parents[1]
    (tmp_path / "runtime/registry").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/finance_read_sources.json").write_text(
        (root / "config/finance_read_sources.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "runtime/registry/accountbooks.json").write_text(json.dumps({"version": 2, "accountbooks": [
        {"key": "company_1", "company_id": "1", "name": "测试公司", "enabled": True, "login_account": "account_1", "session_file": "unused.json"}
    ]}))
    class Client:
        def __init__(self, *args):
            self.company_id, self.dbid = "1", "123"
        def request(self, method, path, query=None, body=None):
            if path == "/basedata/initParams": return {"companyId": "1", "DBID": "123"}
            if path.endswith("/profit"): return {"rows": []}
            if path.endswith("/balance"): return {"rows": []}
            if path.endswith("/cashflow"): return {"main": [], "addendum": []}
            if path.endswith("/voucher/list"): return {"rows": [], "records": 0}
            if path.endswith("/balance-report"):
                return {"item": [
                    {"number": "2202", "name": "应付", "endCredit": 70, "isLeaf": False},
                    {"number": "220201", "accountId": "a", "name": "供应商", "endCredit": 100, "ptDebit": -25, "isLeaf": True},
                    {"number": "220202", "accountId": "b", "name": "无辅助", "endCredit": -30, "isLeaf": True},
                ]}
            if path.endswith("/balance-item-report/query"):
                if body["itemClassIds"] == ["1"]: return []
                return [{"idStr": "vendor", "name": "供应商", "number": "01", "endCredit": 100},
                        {"idStr": "a_vendor", "name": "科目子行", "accountNumber": "220201", "endCredit": 100},
                        {"idStr": None, "name": "合计", "endCredit": 100}]
            raise AssertionError(path)
    snapshot = collect_snapshot(tmp_path, "company_1", f"2026-{selected_month:02}", Client)
    rows = snapshot["sheets"]["往来余额"][4:]
    assert len(rows) == 2
    assert sum((r[9] or 0) - (r[8] or 0) for r in rows) == 70
    assert rows[1][1] == "account:b"
    annual = snapshot["sheets"]["年度分析数据"][4:]
    assert {row[0] for row in annual} == {f'2026-{m:02}' for m in range(1,selected_month+1)}
    assert any(row[:4] == ["2026-01", "subject_credit", "2202", "应付"] for row in annual)
    assert any(row[:4] == ["2026-02", "subject_credit", "2202", "应付"] for row in annual)
    debits = [row for row in annual if row[1] == 'subject_debit_leaf']
    assert {row[2] for row in debits} == {'220201', '220202'}
    assert all(row[4] == -25 for row in debits if row[2] == '220201')
