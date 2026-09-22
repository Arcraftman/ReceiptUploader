"""Materialize supported month settings and document their candidate values."""
from __future__ import annotations

import copy
import json
from pathlib import Path


SHARED_FIELDS = ('analysis_validation', 'ocr_workers', 'llm_workers', 'purpose', 'only_mapped_invoices')


def complete_project_options(project: dict, technical_defaults: dict) -> dict:
    result = copy.deepcopy(project)
    defaults = result['defaults']
    for key in ('ocr_workers', 'llm_workers'):
        defaults.setdefault(key, technical_defaults.get(key, 2))
    for settings in result['sources'].values():
        for key in SHARED_FIELDS:
            settings.setdefault(key, defaults[key])
    return result


DESCRIPTIONS = {
    'version': '项目配置版本，固定为8。',
    'month': '会计月份，例如2026-08。',
    'dataset': '资料来源公司，由month命令选择。',
    'target': '上传目标账套，由month命令选择；生成时须与dataset不同。',
    'company_key': '资料公司键，例如company_17867515。',
    'company_id': '公司ID字符串，必须与所选公司一致。',
    'company_name': '公司或目标账套的完整名称。',
    'accountbook_key': '目标账套键，例如company_23354445。',
    'input': '原始输入文件的读取配置。',
    'usage_filename': '采购用途确认工作簿文件名，默认用途确认信息.xlsx。',
    'usage_column': '用途确认列的Excel列字母，默认E。',
    'defaults': '各来源通用设置；来源级同名字段优先。',
    'sources': '四种来源的独立配置。',
    'sales': '销售发票处理。',
    'purchase': '采购发票处理。',
    'bank': '银行流水与回单处理。',
    'misc': '杂项来源，当前业务链未完成，保持关闭。',
    'enabled': 'true启用，false停用；新月份各来源默认false。银行子项开关只控制该银行。',
    'stage': 'ocr：识别与匹配；match：仅用已有OCR重新匹配银行流水；llm：仅用已有映射分析选模；prepare：生成凭证；verify：仅检查银行待上传凭证PDF绑定；send：提交已有凭证；all：完整执行并可能上传。',
    'analysis_validation': 'strict：严格校验；relaxed：宽松配置。银行固定金额、方向、科目等强制校验不因此取消。',
    'ocr_workers': 'OCR并发数，正整数；生成时继承技术配置的默认值。',
    'llm_workers': '模型分析并发数，正整数；不控制上传并发，银行上传仍逐笔串行。',
    'purpose': '任务用途标签，默认production；属于标识信息，不是上传开关。',
    'only_mapped_invoices': 'true仅处理已映射发票，false不以此限制。银行前期处理可无PDF，上传前verify要求每张待上传凭证绑定PDF。',
    'upload_to_dataset_enabled': 'false：dataset资料上传到target；true：上传到dataset自身账套。默认false，生成后手动更改。',
    'usage_confirmation_enabled': '采购用途确认开关，默认true；false关闭用途确认步骤。',
    'remark_exception': '备注排除关键词数组，包含匹配且忽略空白；命中流水及回单跳过bank。默认仅跳过。“跳过”即使未列入数组也生效。',
    'banks': '按bank_key配置银行；键以小写字母开头，可包含小写字母、数字、下划线和连字符。启用bank来源前至少配置并启用一家银行。',
    'statement_columns': '每家银行的流水列映射，bank_key必须与banks完全一致。',
    'bank_account_number': '当前银行的会计科目编码（数字字符串），不是银行账户号码；必须能在目标账套解析。',
    'split': '回单PDF切割与流水索引规则。',
    'parts_per_page': '每页等高切割份数，1至10。',
    'filename_index_length': '回单索引长度，6至50。',
    'filename_index_prefix': '索引字母前缀，区分大小写，例如V或C。',
    'index_column': '流水索引所在Excel列，例如B；必须与PDF索引一致才能附加对应PDF。',
    'bank_debit_column': '银行借方金额列，例如F；表示本公司资金流出。',
    'bank_credit_column': '银行贷方金额列，例如G；表示本公司资金流入。',
    'counterparty_name_column': '交易方名称列，例如J；用于供应商、客户或个人识别。',
    'remark_column': '业务备注列，例如L；用于退款、手续费、内部转账、工资、费用报销和跳过等关键词匹配。',
}


def schema_fields(schema: dict):
    def walk(node, path):
        if '$ref' in node:
            node = schema['$defs'][node['$ref'].split('/')[-1]]
        if path:
            yield path, node
        for key, child in node.get('properties', {}).items():
            yield from walk(child, f'{path}.{key}' if path else key)
        for child in node.get('patternProperties', {}).values():
            yield from walk(child, f'{path}.<bank_key>')
    return list(walk(schema, ''))


def write_project_options(project_path: Path, schema_path: Path) -> Path:
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    lines = ['# project.json 配置字段与候选值', '',
             '此文件由month命令自动生成，供查阅；运行时只读取project.json。', '',
             '来源级同名字段覆盖defaults。生成器会展开这些字段；修改defaults后，已显式填写的来源字段不会跟随变化。重新生成保留已有值。', '',
             '模型、接口地址和API密钥环境变量配置在config/pipeline.defaults.json，不属于project.json字段。', '',
             '| 字段 | 可选值／格式 | 说明 |', '| --- | --- | --- |']
    for path, node in schema_fields(schema):
        if 'enum' in node:
            choices = ' / '.join(json.dumps(v, ensure_ascii=False) for v in node['enum'])
        elif 'const' in node:
            choices = str(node['const'])
        elif node.get('type') == 'boolean':
            choices = 'true / false'
        else:
            choices = str(node.get('type', 'object'))
            if 'pattern' in node:
                choices += '；格式 ' + node['pattern']
            if 'minimum' in node:
                choices += f"；最小 {node['minimum']}"
            if 'maximum' in node:
                choices += f"；最大 {node['maximum']}"
        description = DESCRIPTIONS.get(path.split('.')[-1], '对应银行的配置项；<bank_key>替换为实际银行键。')
        if isinstance(node.get('type'), list) and 'null' in node['type']:
            description += ' 启用银行时须填写有效列；仅停用银行可使用null。'
        choices = choices.replace("|", "&#124;")
        lines.append(f"| `{path}` | {choices} | {description} |")
    lines += ['', '新增银行时，同时在banks和statement_columns中增加同名bank_key；生成器不会猜测科目编码或流水列。',
              '', '旧exceptions名单、remark_template_map及模型配置不是本项目JSON的有效候选项。', '']
    output = project_path.with_name('project.options.md')
    output.write_text('\n'.join(lines), encoding='utf-8')
    return output
