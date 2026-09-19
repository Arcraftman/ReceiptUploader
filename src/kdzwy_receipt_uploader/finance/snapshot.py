"""Read-only, company-scoped finance snapshots for the Excel client."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from ..company_registry import load_accountbooks, normalize_month
from ..integrations.read_client import ReadClient, CheckFailure, check_identity, month_values, substitute

SCHEMA_VERSION = "1"
MANAGED_SHEETS = ("刷新信息", "公司列表", "利润表", "资产负债表", "现金流量表", "科目余额", "凭证明细", "出纳账", "往来余额", "月度趋势")


def profit_metric(name):
    label = str(name).strip()
    if label in ("一、营业收入", "一、营业总收入"):
        return "营业收入"
    if label in ("减：营业成本", "减:营业成本", "其中：营业成本"):
        return "营业成本"
    if "净利润" in label and "其中" not in label:
        return "净利润"
    return ""


def number(value):
    if value in (None, ""):
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise CheckFailure("金额不是有限数值")
    return float(result)


def walk_rows(rows, child="children"):
    for row in rows:
        yield row
        yield from walk_rows(row.get(child) or [], child)


def normalize_entries(vouchers):
    result = []
    for voucher in vouchers:
        debit = credit = Decimal(0)
        entries = voucher["entries"]
        numbers = [str(e.get("accountNumber") or str(e["accountName"]).split(" ", 1)[0]).split("_")[0] for e in entries]
        cash_only = bool(numbers) and all(n.startswith(("1001", "1002")) for n in numbers)
        for index, (entry, account) in enumerate(zip(entries, numbers), 1):
            if not re.fullmatch(r"\d+", account) or entry["dc"] not in (1, -1):
                raise CheckFailure("凭证科目或借贷方向无效")
            amount = Decimal(str(entry["amount"]))
            if not amount.is_finite():
                raise CheckFailure("凭证金额无效")
            d, c = (amount, Decimal(0)) if entry["dc"] == 1 else (Decimal(0), amount)
            debit += d
            credit += c
            result.append([str(voucher["id"]), index, voucher["date"], voucher["voucherNo"], account,
                           entry["accountName"], entry.get("itemStr", ""), entry.get("explanation", ""),
                           float(d), float(c), entry.get("cur", ""), number(entry.get("amountFor")),
                           "内部划转" if cash_only else "其他收支"])
        if debit != credit or debit != Decimal(str(voucher["debitTotal"])) or credit != Decimal(str(voucher["creditTotal"])):
            raise CheckFailure("凭证列表分录不平或与合计不符")
    return result


def sheet(title, note, headers, rows):
    return [[title], [note], [], headers, *rows]


def collect_snapshot(root: Path, company: str, month: str, client_factory=ReadClient):
    normalize_month(month)
    books = load_accountbooks(root / "runtime/registry/accountbooks.json")
    if company not in books or not books[company].enabled:
        raise CheckFailure("请选择已启用的公司编号")
    book = books[company]
    client = client_factory(root / book.session_file, book.company_id, book.name)
    check_identity(client.request("GET", "/basedata/initParams", {"m": "getSystemParams"}), client.company_id, client.dbid)
    cases = json.loads((root / "config/finance_read_sources.json").read_text())
    def fetch(key, requested_month=month, overrides=None):
        case = cases[key]
        vals = month_values(requested_month)
        query = substitute(case.get("query", {}), vals)
        body = substitute(case.get("body"), vals)
        (body if body is not None else query).update(overrides or {})
        return client.request(case["method"], case["path"].replace("{dbId}", client.dbid), query, body)

    profits = fetch("profit_sheet")
    balance = fetch("balance_sheet")
    cashflow = fetch("cashflow")
    subjects = fetch("subject_balance", overrides={"isIncludeItem": False, "toLevel": 10})
    vouchers = []
    seen = set()
    total = None
    for page in range(1, 1001):
        response = fetch("voucher_list", overrides={"page": page, "pageSize": 100})
        count = int(response["records"])
        if total is not None and count != total:
            raise CheckFailure("刷新过程中凭证数量变化，请重试")
        total = count
        rows = response["rows"]
        for row in rows:
            rid = str(row["id"])
            if rid in seen or str(row["yearPeriod"]) != month.replace("-", ""):
                raise CheckFailure("凭证分页重复或期间不符")
            seen.add(rid)
        vouchers.extend(rows)
        if len(vouchers) == total:
            break
        if not rows or len(vouchers) > total:
            raise CheckFailure("凭证分页不完整")
    else:
        raise CheckFailure("凭证数量超过单次刷新上限")
    entries = normalize_entries(vouchers)
    # Report figures come from the official reports, not from summing journal debits.
    note = f"{book.name}；{month}；金额单位：人民币元"
    data = {}
    data["利润表"] = sheet("利润表", note, ["项目编码", "项目", "本月金额", "本年累计", "上年累计", "统计分类"],
                           [[r["reportItem"], r["name"], number(r.get("balance")), number(r.get("curBalance")), number(r.get("preBalance")), profit_metric(r["name"])] for r in profits["rows"]])
    data["资产负债表"] = sheet("资产负债表", note, ["资产", "期末余额", "年初余额", "负债与权益", "期末余额", "年初余额"],
                              [[r["asset"]["name"], number(r["asset"].get("balance")), number(r["asset"].get("preBalance")),
                                r["liability"]["name"], number(r["liability"].get("balance")), number(r["liability"].get("preBalance"))] for r in balance["rows"]])
    data["现金流量表"] = sheet("现金流量表", note + "；原系统报表，不以银行净额替代", ["区块", "项目编码", "项目", "balance", "curBalance", "preBalance"],
                              [[section, r.get("reportItem", ""), r["name"], number(r.get("balance")), number(r.get("curBalance")), number(r.get("preBalance"))]
                               for section in ("main", "addendum") for r in cashflow[section]])
    subject_rows = list(walk_rows(subjects["item"]))
    data["科目余额"] = sheet("科目余额", note + "；各级科目不能重复求和", ["科目编码", "科目", "期初借方", "期初贷方", "本期借方", "本期贷方", "期末借方", "期末贷方", "末级"],
                            [[str(r["number"]), r["name"], *[number(r.get(k)) for k in ("beginDebit", "beginCredit", "ptDebit", "ptCredit", "endDebit", "endCredit")], bool(r.get("isLeaf"))] for r in subject_rows])
    data["凭证明细"] = sheet("凭证明细", note, ["凭证ID", "分录序号", "日期", "凭证号", "科目编码", "科目名称", "辅助项目", "摘要", "借方", "贷方", "币种", "原币金额", "收支类型"], entries)
    cash_entries = [r for r in entries if r[4].startswith(("1001", "1002"))]
    data["出纳账"] = sheet("出纳账（总账口径）", note + "；从现金及银行凭证读取；不是未制证银行流水或银行对账结果", data["凭证明细"][3], cash_entries)
    auxiliary = []
    for cls, account, kind in (("1", "1122", "应收"), ("5", "2202", "应付")):
        rows = fetch("auxiliary_balance", overrides={"itemClassIds": [cls], "accountNumber": account, "includeAccount": True})
        covered = {str(r.get("accountNumber")) for r in walk_rows(rows, "child") if r.get("accountNumber")}
        for r in walk_rows(rows, "child"):
            if r.get("child") or not r.get("idStr") or r.get("accountNumber"):
                continue
            auxiliary.append([kind, str(r["idStr"]), str(r.get("number", "")), r["name"],
                              *[number(r.get(k)) for k in ("beginDebit", "beginCredit", "debit", "credit", "endDebit", "endCredit")]])
        for r in subject_rows:
            if r.get("isLeaf") and str(r["number"]).startswith(account) and str(r["number"]) not in covered:
                auxiliary.append([kind, "account:" + str(r["accountId"]), str(r["number"]), r["name"] + "（科目余额，无辅助项目）",
                                  *[number(r.get(k)) for k in ("beginDebit", "beginCredit", "ptDebit", "ptCredit", "endDebit", "endCredit")]])
        roots = [r for r in subject_rows if str(r["number"]) == account]
        if len(roots) == 1:
            detail_total = sum(Decimal(str(r[8] or 0)) - Decimal(str(r[9] or 0)) for r in auxiliary if r[0] == kind)
            ledger_total = Decimal(str(roots[0].get("endDebit") or 0)) - Decimal(str(roots[0].get("endCredit") or 0))
            if abs(detail_total - ledger_total) > Decimal("0.01"):
                raise CheckFailure(f"{kind}辅助余额与总账不符，停止刷新")
    data["往来余额"] = sheet("往来余额", note + "；应收1122、应付2202；未核销单据和到期日尚未由接口提供", ["类型", "核算项目ID组合", "编码", "名称", "期初借方", "期初贷方", "本期借方", "本期贷方", "期末借方", "期末贷方"], auxiliary)
    trend = []
    for m in range(1, int(month[-2:]) + 1):
        period = f"{month[:4]}-{m:02}"
        report = profits if period == month else fetch("profit_sheet", period)
        for row in report["rows"]:
            trend.append([period, row["reportItem"], row["name"], number(row.get("balance")), profit_metric(row["name"])])
    data["月度趋势"] = sheet("利润月度明细", f"{book.name}；年初至{month}；人民币元", ["月份", "项目编码", "项目", "本月金额", "统计分类"], trend)
    check_identity(client.request("GET", "/basedata/initParams", {"m": "getSystemParams"}), client.company_id, client.dbid)
    refreshed = datetime.now(timezone.utc).isoformat()
    data["公司列表"] = sheet("可选公司", "使用已启用的本地账套注册表", ["公司编号", "公司名称"], [[key, b.name] for key, b in books.items() if b.enabled])
    checks = []
    for title, report in (("利润表", profits), ("资产负债表", balance), ("现金流量表", cashflow)):
        for key in ("isBalance", "isAbsent", "isRepeat", "needReload", "isEmpty", "status"):
            if key in report:
                checks.append([f"{title}.{key}", str(report[key])])
    data["刷新信息"] = sheet("刷新信息", "原系统可能存在未结账或报表校验提示；读取成功不代表已审计", ["字段", "值"],
                           [["schema", SCHEMA_VERSION], ["company", company], ["company_name", book.name], ["month", month], ["refreshed_at_utc", refreshed],
                            ["voucher_count", len(vouchers)], ["entry_count", len(entries)], ["aging", "账龄需人工未核销单据输入；未分配余额不推算天数"], *checks])
    return {"schema": SCHEMA_VERSION, "company": company, "month": month, "refreshed_at": refreshed, "sheets": {name: data[name] for name in MANAGED_SHEETS}}


def spreadsheet_xml(snapshot):
    """Excel 2003 XML transport. Strings never become formulas or links."""
    ns = "urn:schemas-microsoft-com:office:spreadsheet"
    ET.register_namespace("ss", ns)
    wb = ET.Element(f"{{{ns}}}Workbook")
    for name, rows in snapshot["sheets"].items():
        if name not in MANAGED_SHEETS:
            raise CheckFailure("不允许覆盖人工输入工作表")
        ws = ET.SubElement(wb, f"{{{ns}}}Worksheet", {f"{{{ns}}}Name": name})
        table = ET.SubElement(ws, f"{{{ns}}}Table")
        for values in rows:
            row = ET.SubElement(table, f"{{{ns}}}Row")
            for column, value in enumerate(values):
                cell = ET.SubElement(row, f"{{{ns}}}Cell")
                if value is None:
                    continue
                typ = "Boolean" if isinstance(value, bool) else "Number" if isinstance(value, (int, float)) else "String"
                text = str(int(value)) if isinstance(value, bool) else str(value)
                if name in ("凭证明细", "出纳账") and column == 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                    typ, text = "DateTime", text + "T00:00:00.000"
                data = ET.SubElement(cell, f"{{{ns}}}Data", {f"{{{ns}}}Type": typ})
                data.text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return ET.tostring(wb, encoding="utf-8", xml_declaration=True)
