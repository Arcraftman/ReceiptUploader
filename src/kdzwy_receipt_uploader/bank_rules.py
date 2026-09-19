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


def employee_payment_kind(row: Mapping[str, Any]) -> str:
    """Route only explicit, unambiguous employee cash outflows."""
    if not is_person_name(row.get("counterpartyName")) or row.get("flowDirection") != "outflow":
        return ""
    remark = str(row.get("remark") or "")
    salary, reimbursement = "工资" in remark, "费用报销" in remark
    if salary == reimbursement:
        return ""
    return "salary" if salary else "reimbursement"
