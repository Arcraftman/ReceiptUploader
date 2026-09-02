"""Match per-bank statement rows to OCR'd receipt PDFs by configured index."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Mapping

from openpyxl.utils.cell import column_index_from_string

from .xlsx_cache import load_read_only_workbook


class BankStatementMatchError(RuntimeError):
    pass


_CHINESE_PERSON_NAME_PATTERN = re.compile(r"[\u3400-\u9fff]{2,4}")
_COMMON_CHINESE_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏窦章云苏潘葛奚范彭郎鲁韦昌马苗方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包诸左石崔吉龚程嵇邢裴陆荣翁荀羊惠甄曲家封芮储靳汲邴糜井段富巫乌焦巴弓牧山谷车侯全班仰秋仲伊宫宁仇栾甘厉戎祖武符刘景詹束龙叶幸司韶郜黎薄印宿白怀蒲从鄂索咸赖卓蔺屠蒙池乔胥苍双闻党翟谭贡劳姬申扶冉宰雍桑桂牛寿通边扈燕冀浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾居衡步都耿满弘匡国文寇广东欧沃利蔚越隆师巩聂晁勾敖融冷辛那简饶空曾沙鞠须丰巢关相查后荆红游竺权盖益桓公"
)
_COMPOUND_CHINESE_SURNAMES = (
    "欧阳",
    "司马",
    "上官",
    "诸葛",
    "东方",
    "独孤",
    "南宫",
    "万俟",
    "闻人",
    "夏侯",
    "皇甫",
    "尉迟",
    "公羊",
    "赫连",
    "澹台",
    "公冶",
    "宗政",
    "濮阳",
    "淳于",
    "单于",
    "太叔",
    "申屠",
    "公孙",
    "仲孙",
    "轩辕",
    "令狐",
    "钟离",
    "宇文",
    "长孙",
    "慕容",
    "司徒",
    "司空",
)


def is_person_name(value: object) -> bool:
    """Conservatively identify short Chinese personal names before bank OCR."""
    name = str(value or "").strip()
    if _CHINESE_PERSON_NAME_PATTERN.fullmatch(name) is None:
        return False
    return name[0] in _COMMON_CHINESE_SURNAMES or name.startswith(
        _COMPOUND_CHINESE_SURNAMES
    )


def _person_name_marker(
    row: Mapping[str, Any], bank_account_number: str
) -> dict[str, Any]:
    marker = {
        key: value
        for key, value in row.items()
        if key not in {"supplierName", "customerName", "customName"}
    }
    marker.update(
        {
            "bankAccountNumber": bank_account_number,
            "counterpartyType": "person",
            "itemClass": "人员",
            "markerOnly": True,
            "downstreamEligible": False,
            "markerReason": "person_name",
        }
    )
    return marker


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _index_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _matches_index(value: str, length: int, prefix: str) -> bool:
    return (
        len(value) == length
        and value.startswith(prefix)
        and re.fullmatch(r"[A-Za-z0-9]+", value) is not None
    )


def _parse_amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, (int, float)):
        amount = Decimal(str(value))
    else:
        text = str(value).strip()
        if not text:
            return None
        compact_identifier = re.sub(r"\s+", "", text)
        if re.fullmatch(r"\d{15,}", compact_identifier):
            return None
        text = (
            text.replace(",", "")
            .replace("，", "")
            .replace("￥", "")
            .replace("¥", "")
            .replace("元", "")
            .strip()
        )
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1].strip()
        try:
            amount = Decimal(text)
        except InvalidOperation:
            return None
        if negative:
            amount = -amount
    return amount if amount.is_finite() else None


def _amount_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def extract_invoice_like_numbers(value: object) -> list[str]:
    """Extract numeric references only when the whole inactive cell is numeric text."""
    text = str(value or "").strip()
    if not text:
        return []
    remainder = re.sub(r"\d{8,20}|[\s,，;；、]+", "", text)
    if remainder:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for match in re.finditer(r"(?<!\d)\d{8,20}(?!\d)", text):
        number = match.group(0)
        if number not in seen:
            seen.add(number)
            result.append(number)
    return result


def _flow_fields(debit_raw: Any, credit_raw: Any) -> dict[str, Any]:
    bank_debit = _parse_amount(debit_raw)
    bank_credit = _parse_amount(credit_raw)
    debit_active = bank_debit is not None and bank_debit != 0
    credit_active = bank_credit is not None and bank_credit != 0
    if debit_active and not credit_active:
        direction = "outflow"
        our_debit = None
        our_credit = bank_debit
        direction_error = ""
    elif credit_active and not debit_active:
        direction = "inflow"
        our_debit = bank_credit
        our_credit = None
        direction_error = ""
    else:
        direction = "ambiguous"
        our_debit = None
        our_credit = None
        direction_error = "银行借方与贷方必须恰好一方包含非零金额"
    transaction_amount = our_debit if our_debit is not None else our_credit
    amount_source = (
        "bank_statement.ourDebitAmount"
        if our_debit is not None
        else "bank_statement.ourCreditAmount"
        if our_credit is not None
        else ""
    )
    return {
        "flowDirection": direction,
        "bankDebitRaw": _json_value(debit_raw),
        "bankDebitAmount": _amount_text(bank_debit),
        "bankCreditRaw": _json_value(credit_raw),
        "bankCreditAmount": _amount_text(bank_credit),
        "ourDebitAmount": _amount_text(our_debit),
        "ourCreditAmount": _amount_text(our_credit),
        "transactionAmount": _amount_text(transaction_amount),
        "statementAmount": _amount_text(transaction_amount),
        "amountSource": amount_source,
        "amountValidated": transaction_amount is not None and not direction_error,
        "directionError": direction_error,
        "invoiceNumbers": (
            extract_invoice_like_numbers(debit_raw) if direction == "inflow" else []
        ),
    }


def _required_columns(bank_key: str, bank_config: Mapping[str, Any]) -> tuple[str, str, str, str]:
    raw_columns = bank_config.get("statement_columns")
    if not isinstance(raw_columns, Mapping):
        raise BankStatementMatchError(
            f"project.json sources.bank.banks.{bank_key}.statement_columns 必须是对象"
        )
    labels = (
        "index_column",
        "bank_debit_column",
        "bank_credit_column",
        "counterparty_name_column",
    )
    values: list[str] = []
    for label in labels:
        value = str(raw_columns.get(label) or "").strip().upper()
        if not value:
            raise BankStatementMatchError(
                f"project.json sources.bank.banks.{bank_key}.statement_columns.{label} 尚未配置"
            )
        try:
            column_index_from_string(value)
        except ValueError as exc:
            raise BankStatementMatchError(
                f"{bank_key}.{label} 不是有效 Excel 列：{value}"
            ) from exc
        values.append(value)
    return values[0], values[1], values[2], values[3]


def _read_statement_rows(
    statement_path: Path,
    bank_key: str,
    bank_config: Mapping[str, Any],
    config_company: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    if not statement_path.is_file():
        raise BankStatementMatchError(f"配置中的银行流水 Excel 不存在：{statement_path}")
    index_column, debit_column, credit_column, counterparty_column = _required_columns(bank_key, bank_config)
    index_col = column_index_from_string(index_column)
    debit_col = column_index_from_string(debit_column)
    credit_col = column_index_from_string(credit_column)
    counterparty_col = column_index_from_string(counterparty_column)
    max_col = max(index_col, debit_col, credit_col, counterparty_col)
    split_config = bank_config.get("split")
    if not isinstance(split_config, Mapping):
        raise BankStatementMatchError(f"{bank_key}.split 必须是对象")
    configured_length = int(split_config["filename_index_length"])
    configured_prefix = str(split_config["filename_index_prefix"])
    rows: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    workbook = load_read_only_workbook(statement_path)
    try:
        for worksheet in workbook.worksheets:
            for row_number, values in enumerate(
                worksheet.iter_rows(min_col=1, max_col=max_col, values_only=True),
                start=1,
            ):
                raw_index = values[index_col - 1]
                index = _index_text(raw_index)
                if not index:
                    continue
                if not _matches_index(index, configured_length, configured_prefix):
                    ignored.append({
                        "sheet": worksheet.title,
                        "row": row_number,
                        "value": _json_value(raw_index),
                    })
                    continue
                flow = _flow_fields(values[debit_col - 1], values[credit_col - 1])
                counterparty_name = str(values[counterparty_col - 1] or "").strip()
                if flow["flowDirection"] == "outflow":
                    counterparty_type = "supplier"
                    item_class = "供应商"
                elif flow["flowDirection"] == "inflow":
                    counterparty_type = "customer"
                    item_class = "客户"
                else:
                    counterparty_type = ""
                    item_class = ""
                rows.append({
                    "index": index,
                    "configCompany": config_company,
                    "counterpartyName": counterparty_name,
                    "counterpartyType": counterparty_type,
                    "itemClass": item_class,
                    "counterpartyTypeHint": counterparty_type,
                    "itemClassHint": item_class,
                    "counterpartyRoleSource": "statement_direction_hint",
                    **({"supplierName": counterparty_name} if counterparty_type == "supplier" else {}),
                    **({"customerName": counterparty_name, "customName": counterparty_name} if counterparty_type == "customer" else {}),
                    "statement": {
                        "xlsx": str(statement_path.resolve()),
                        "sheet": worksheet.title,
                        "row": row_number,
                        "indexColumn": index_column,
                        "bankDebitColumn": debit_column,
                        "bankCreditColumn": credit_column,
                        "counterpartyNameColumn": counterparty_column,
                    },
                    **flow,
                })
    finally:
        workbook.close()
    if not rows:
        raise BankStatementMatchError(
            f"{statement_path.name} 的 {index_column} 列没有符合 {configured_prefix} / {configured_length} 位规则的流水索引"
        )
    return rows, ignored, {
        "index_column": index_column,
        "bank_debit_column": debit_column,
        "bank_credit_column": credit_column,
        "counterparty_name_column": counterparty_column,
    }


def _read_receipts(
    ocr_report: Mapping[str, Any],
    bank_key: str,
    bank_config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output_directory = Path(str(ocr_report.get("outputDirectory") or ""))
    artifacts = ocr_report.get("artifacts")
    if not output_directory.is_dir() or not isinstance(artifacts, list):
        raise BankStatementMatchError("银行 OCR 报告缺少有效 outputDirectory 或 artifacts")
    split_config = bank_config["split"]
    configured_length = int(split_config["filename_index_length"])
    configured_prefix = str(split_config["filename_index_prefix"])
    recognized: list[dict[str, Any]] = []
    bank_exceptions: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or str(artifact.get("bankKey")) != bank_key:
            continue
        source_pdf = Path(str(artifact.get("sourcePdf") or ""))
        artifact_relative = Path(str(artifact.get("artifactDirectory") or ""))
        index = source_pdf.stem
        record = {
            "index": index if _matches_index(index, configured_length, configured_prefix) else "",
            "receipt": {
                "pdf": str(source_pdf.resolve()),
                "ocrText": str((output_directory / artifact_relative / "ocr.txt").resolve()),
                "ocrMetadata": str(Path(str(artifact.get("metadata") or "")).resolve()),
            },
        }
        if record["index"]:
            recognized.append(record)
        else:
            bank_exceptions.append(record)
    return recognized, bank_exceptions


def collect_person_name_exclusions(
    bank_configs: Mapping[str, Mapping[str, Any]],
    input_directory: Path,
) -> dict[str, set[str]]:
    """Return statement indexes whose configured counterparty cell is a person name."""
    exclusions: dict[str, set[str]] = {}
    for bank_key, bank_config in sorted(bank_configs.items()):
        statement_rows, _, _ = _read_statement_rows(
            input_directory / f"{bank_key}.xlsx",
            bank_key,
            bank_config,
            "",
        )
        exclusions[bank_key] = {
            str(row["index"])
            for row in statement_rows
            if is_person_name(row.get("counterpartyName"))
        }
    return exclusions


def read_bank_statement_rows(
    bank_configs: Mapping[str, Mapping[str, Any]],
    input_directory: Path,
    config_company: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Read normalized statement rows once for pre-OCR exception filtering."""
    result: dict[str, list[dict[str, Any]]] = {}
    for bank_key, bank_config in sorted(bank_configs.items()):
        rows, _, _ = _read_statement_rows(
            input_directory / f"{bank_key}.xlsx",
            bank_key,
            bank_config,
            config_company,
        )
        result[bank_key] = rows
    return result


def match_bank_statements(
    bank_configs: Mapping[str, Mapping[str, Any]],
    input_directory: Path,
    ocr_report: Mapping[str, Any],
    map_path: Path,
    report_path: Path,
    config_company: str = "",
    excluded_statement_indices: Mapping[str, set[str]] | None = None,
) -> dict[str, Any]:
    if not isinstance(bank_configs, Mapping) or not bank_configs:
        raise BankStatementMatchError("project.json sources.bank.banks 必须至少配置一家银行")
    bank_map: dict[str, Any] = {
        "version": 1,
        "source": "project.json.sources.bank.banks",
        "configCompany": config_company,
        "banks": {},
    }
    bank_reports: dict[str, Any] = {}
    total_statement_rows = 0
    total_recognized_receipts = 0
    total_bank_exception_receipts = 0
    total_matches = 0
    total_unmatched_statements = 0
    total_unmatched_receipts = 0
    total_skipped_person_names = 0
    total_direction_errors = 0
    total_duplicates = 0
    total_exception_filtered = 0
    normalized_exception_exclusions = {
        str(bank_key): {str(index) for index in indexes}
        for bank_key, indexes in (excluded_statement_indices or {}).items()
    }

    for bank_key, bank_config in sorted(bank_configs.items()):
        bank_account_number = str(bank_config.get("bank_account_number") or "").strip()
        if not re.fullmatch(r"[0-9]+", bank_account_number):
            raise BankStatementMatchError(
                f"project.json sources.bank.banks.{bank_key}.bank_account_number 尚未正确配置"
            )
        statement_path = input_directory / f"{bank_key}.xlsx"
        all_statement_rows, ignored_rows, normalized_columns = _read_statement_rows(
            statement_path, bank_key, bank_config, config_company
        )
        exception_indexes = normalized_exception_exclusions.get(bank_key, set())
        exception_filtered_statements = [
            row for row in all_statement_rows if str(row.get("index")) in exception_indexes
        ]
        skipped_person_name_statements = [
            _person_name_marker(row, bank_account_number)
            for row in all_statement_rows
            if str(row.get("index")) not in exception_indexes
            and is_person_name(row.get("counterpartyName"))
        ]
        statement_rows = [
            row
            for row in all_statement_rows
            if str(row.get("index")) not in exception_indexes
            and not is_person_name(row.get("counterpartyName"))
        ]
        receipts, bank_exception_receipts = _read_receipts(
            ocr_report, bank_key, bank_config
        )
        statements_by_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
        receipts_by_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in statement_rows:
            statements_by_index[row["index"]].append(row)
        for receipt in receipts:
            receipts_by_index[receipt["index"]].append(receipt)

        matches: dict[str, Any] = {}
        unmatched_statements: list[dict[str, Any]] = []
        unmatched_receipt_rows: list[dict[str, Any]] = list(bank_exception_receipts)
        duplicates: list[dict[str, Any]] = []
        direction_errors: list[dict[str, Any]] = []
        for index in sorted(set(statements_by_index) | set(receipts_by_index)):
            statement_candidates = statements_by_index.get(index, [])
            receipt_candidates = receipts_by_index.get(index, [])
            if len(statement_candidates) > 1 or len(receipt_candidates) > 1:
                duplicates.append({
                    "index": index,
                    "statementRows": [item["statement"] for item in statement_candidates],
                    "receipts": [item["receipt"] for item in receipt_candidates],
                })
                continue
            if not statement_candidates:
                unmatched_receipt_rows.extend(receipt_candidates)
                continue
            if not receipt_candidates:
                unmatched_statements.extend([
                    {
                        **item,
                        "bankAccountNumber": bank_account_number,
                        "markerOnly": True,
                        "downstreamEligible": False,
                    }
                    for item in statement_candidates
                ])
                continue
            statement = statement_candidates[0]
            if statement["directionError"]:
                direction_errors.append({
                    "index": index,
                    "error": statement["directionError"],
                    "statement": statement["statement"],
                    "downstreamEligible": False,
                })
                continue
            entry = {
                "index": index,
                "flowDirection": statement["flowDirection"],
                "configCompany": config_company,
                "bankAccountNumber": bank_account_number,
                "counterpartyName": statement["counterpartyName"],
                "counterpartyType": statement["counterpartyType"],
                "itemClass": statement["itemClass"],
                "counterpartyTypeHint": statement["counterpartyTypeHint"],
                "itemClassHint": statement["itemClassHint"],
                "counterpartyRoleSource": statement["counterpartyRoleSource"],
                **({"supplierName": statement["supplierName"]} if statement.get("supplierName") else {}),
                **({"customerName": statement["customerName"], "customName": statement["customName"]} if statement.get("customerName") else {}),
                "bankDebitRaw": statement["bankDebitRaw"],
                "bankDebitAmount": statement["bankDebitAmount"],
                "bankCreditRaw": statement["bankCreditRaw"],
                "bankCreditAmount": statement["bankCreditAmount"],
                "ourDebitAmount": statement["ourDebitAmount"],
                "ourCreditAmount": statement["ourCreditAmount"],
                "transactionAmount": statement["transactionAmount"],
                "statementAmount": statement["statementAmount"],
                "amountSource": statement["amountSource"],
                "amountValidated": statement["amountValidated"],
                "invoiceNumbers": statement["invoiceNumbers"],
                "statement": statement["statement"],
                "receipt": receipt_candidates[0]["receipt"],
            }
            matches[index] = entry

        bank_map["banks"][bank_key] = {
            "bankAccountNumber": bank_account_number,
            "statementFile": str(statement_path.resolve()),
            "statementColumns": normalized_columns,
            "entries": matches,
        }
        bank_summary = {
            "statementRowCount": len(all_statement_rows),
            "eligibleStatementRowCount": len(statement_rows),
            "exceptionFilteredStatementCount": len(exception_filtered_statements),
            "skippedPersonNameCount": len(skipped_person_name_statements),
            "recognizedReceiptCount": len(receipts),
            "bankExceptionReceiptCount": len(bank_exception_receipts),
            "matchedCount": len(matches),
            "unmatchedStatementCount": len(unmatched_statements),
            "unmatchedReceiptCount": len(unmatched_receipt_rows),
            "duplicateIndexCount": len(duplicates),
            "directionErrorCount": len(direction_errors),
            "ignoredIndexValueCount": len(ignored_rows),
        }
        bank_reports[bank_key] = {
            "bankAccountNumber": bank_account_number,
            "statementFile": str(statement_path.resolve()),
            "statementColumns": normalized_columns,
            "summary": bank_summary,
            "unmatchedStatements": unmatched_statements,
            "skippedPersonNameStatements": skipped_person_name_statements,
            "exceptionFilteredStatements": exception_filtered_statements,
            "unmatchedReceipts": unmatched_receipt_rows,
            "duplicateIndexes": duplicates,
            "directionErrors": direction_errors,
            "ignoredIndexValues": ignored_rows,
        }
        total_statement_rows += len(all_statement_rows)
        total_recognized_receipts += len(receipts)
        total_bank_exception_receipts += len(bank_exception_receipts)
        total_matches += len(matches)
        total_unmatched_statements += len(unmatched_statements)
        total_unmatched_receipts += len(unmatched_receipt_rows)
        total_skipped_person_names += len(skipped_person_name_statements)
        total_direction_errors += len(direction_errors)
        total_duplicates += len(duplicates)
        total_exception_filtered += len(exception_filtered_statements)

    summary = {
        "bankCount": len(bank_reports),
        "statementRowCount": total_statement_rows,
        "recognizedReceiptCount": total_recognized_receipts,
        "bankExceptionReceiptCount": total_bank_exception_receipts,
        "matchedCount": total_matches,
        "unmatchedStatementCount": total_unmatched_statements,
        "unmatchedReceiptCount": total_unmatched_receipts,
        "skippedPersonNameCount": total_skipped_person_names,
        "duplicateIndexCount": total_duplicates,
        "directionErrorCount": total_direction_errors,
        "exceptionFilteredStatementCount": total_exception_filtered,
    }
    if total_duplicates or total_direction_errors:
        status = "partial"
    elif total_unmatched_statements or total_unmatched_receipts:
        status = "ok_with_unmatched"
    else:
        status = "ok"
    report = {
        "version": 1,
        "status": status,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "configSource": "project.json.sources.bank.banks",
        "configCompany": config_company,
        "mapFile": str(map_path.resolve()),
        "banks": bank_reports,
        "summary": summary,
    }
    _atomic_write_json(map_path, bank_map)
    _atomic_write_json(report_path, report)
    return report
