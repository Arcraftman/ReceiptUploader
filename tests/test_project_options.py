import json
from pathlib import Path

from kdzwy_receipt_uploader.commands.initialize_company_month import normalize_month_defaults, normalize_source_settings
from kdzwy_receipt_uploader.project_options import complete_project_options, write_project_options, schema_fields, DESCRIPTIONS


def test_complete_fields_preserves_overrides_and_inherits_technical_defaults():
    original = {'defaults': normalize_month_defaults({'ocr_workers': 3}),
                'sources': normalize_source_settings({'bank': {'llm_workers': 8, 'remark_exception': ['单独处理']}})}
    completed = complete_project_options(original, {'ocr_workers': 2, 'llm_workers': 4})
    assert completed['defaults']['ocr_workers'] == 3
    assert completed['defaults']['llm_workers'] == 4
    assert completed['sources']['sales']['llm_workers'] == 4
    assert completed['sources']['bank']['llm_workers'] == 8
    assert completed['sources']['bank']['remark_exception'] == ['单独处理']
    assert 'llm_workers' not in original['defaults']
    assert all(not source['enabled'] for source in completed['sources'].values())
    assert completed == complete_project_options(completed, {'ocr_workers': 9, 'llm_workers': 9})


def test_options_cover_all_schema_fields_and_enum_choices(tmp_path):
    schema_path = Path(__file__).parents[1] / 'schema/project.schema.json'
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    output = write_project_options(tmp_path / 'project.json', schema_path)
    text = output.read_text(encoding='utf-8')
    for path, node in schema_fields(schema):
        assert f'`{path}`' in text
        assert path.endswith('<bank_key>') or path.split('.')[-1] in DESCRIPTIONS
        for choice in node.get('enum', []):
            assert json.dumps(choice, ensure_ascii=False) in text
    assert 'sources.bank.banks.<bank_key>.split.filename_index_length' in text
    assert '默认false' in text
    assert '不控制上传并发' in text
