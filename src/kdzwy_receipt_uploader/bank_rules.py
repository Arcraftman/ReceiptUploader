"""Pure bank counterparty and employee payment classification rules."""
from __future__ import annotations

import re
from typing import Any, Mapping

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


def is_supplier_refund(row: Mapping[str, Any]) -> bool:
    """Match a business keyword within the statement remark."""
    return "退款" in str(row.get("remark") or "")


def internal_transfer_account(row: Mapping[str, Any]) -> str:
    remark = str(row.get("remark") or "")
    matches = re.findall(r"内部转账\s*([0-9]+)", remark)
    return matches[0] if len(matches) == 1 and remark.count("内部转账") == 1 else ""


def employee_payment_kind(row: Mapping[str, Any]) -> str:
    """Route only explicit, unambiguous employee cash outflows."""
    if not is_person_name(row.get("counterpartyName")) or row.get("flowDirection") != "outflow":
        return ""
    remark = str(row.get("remark") or "")
    salary, reimbursement = "工资" in remark, "报销" in remark
    if salary == reimbursement:
        return ""
    return "salary" if salary else "reimbursement"


def is_bank_fee(row: Mapping[str, Any]) -> bool:
    return "手续费" in str(row.get("remark") or "")


DEFAULT_REMARK_EXCEPTIONS = ["跳过"]


def remark_exception_match(remark: object, keywords: list[str]) -> str:
    text = re.sub(r"\s+", "", str(remark or ""))
    if "跳过" in text:
        return "跳过"
    return next((keyword for keyword in keywords if re.sub(r"\s+", "", keyword)
                 and re.sub(r"\s+", "", keyword) in text), "")


def is_personal_reimbursement(row: Mapping[str, Any]) -> bool:
    return row.get("configCompany") == "上海微誉信息技术有限公司" and employee_payment_kind(row) == "reimbursement"


def flatten_account_rows(rows: Any) -> list[Mapping[str, Any]]:
    result = {}
    def visit(items):
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, Mapping):
                continue
            if item.get("number"):
                result[(str(item["number"]), str(item.get("id") or ""))] = item
            visit(item.get("child", []))
            visit(item.get("children", []))
    visit(rows)
    return list(result.values())


def resolve_employee_payable(row: Mapping[str, Any], accounts: Any) -> dict[str, str]:
    """Resolve one exact person leaf beneath 2241, at any account depth."""
    name = str(row.get("counterpartyName") or "").strip()
    rows = flatten_account_rows(accounts)
    matches = []
    for account in rows:
        number = str(account.get("number") or "")
        leaf_name = str(account.get("name") or account.get("fullName") or "").split("_")[-1].strip()
        if not re.fullmatch(r"2241[0-9]+", number) or leaf_name != name:
            continue
        if account.get("child") or account.get("children") or any(
            str(other.get("number") or "").startswith(number) and str(other.get("number")) != number for other in rows
        ):
            continue
        if account.get("id") in (None, "", 0, "0"):
            continue
        matches.append({"personName": name, "accountNumber": number,
                        "accountId": str(account["id"]), "accountName": leaf_name})
    if len(matches) != 1:
        raise ValueError(f"2241人员明细科目无法唯一匹配：{name}，候选={matches}")
    return matches[0]


def personal_reimbursement_summary(row: Mapping[str, Any]) -> str:
    name = str(row.get("counterpartyName") or "").strip()
    remark = str(row.get("remark") or "").strip().replace("费用报销", "报销")
    return remark if remark.startswith(name) else name + remark


MANUAL_BANK_REMARKS = ("增值税缴税", "社保缴税", "公积金", "个税缴税")


def manual_bank_remark(remark: object) -> str:
    """Classify for manual processing, without excluding from upload."""
    text = re.sub(r"\s+", "", str(remark or ""))
    return next((keyword for keyword in MANUAL_BANK_REMARKS if keyword in text), "")
