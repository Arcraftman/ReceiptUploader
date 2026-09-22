import json
from unittest.mock import Mock
import pytest
from kdzwy_receipt_uploader.finance.interpretation import interpret, validate_payload, interpretation_xml


def payload():
    return {'company':'company_1','month':'2026-01','rows':[['2026-01',100,60,1,2,3,6,40,30,25,.4,.25,.06,100,25,None]]}


def test_six_reports_cached_and_only_aggregate_data_sent(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-placeholder')
    response = Mock(status_code=200)
    response.json.return_value = {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'texts':['仅作单元测试的汇总解读。']*6})}}]}
    post = Mock(return_value=response)
    result = interpret(tmp_path,payload(),post)
    assert len(result['texts']) == 6
    sent = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert set(sent)=={'period','charts','columns','rows','verified_facts'}
    assert len(sent['charts'])==6 and 'company' not in sent
    assert interpret(tmp_path,payload(),post)==result
    assert post.call_count==1
    assert b'<text id="6">' in interpretation_xml(result)


@pytest.mark.parametrize('bad', [float('nan'),'arbitrary text',True])
def test_invalid_metrics_rejected(bad):
    data = payload(); data['rows'][0][1] = bad
    with pytest.raises(ValueError): validate_payload(data)


def test_missing_key_and_malformed_reply_fail_without_cache(tmp_path,monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    with pytest.raises(ValueError,match='未配置'): interpret(tmp_path,payload(),Mock())
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-placeholder')
    response = Mock(status_code=200)
    response.json.return_value = {'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
    with pytest.raises(ValueError,match='不完整'): interpret(tmp_path,payload(),Mock(return_value=response))
    assert not list(tmp_path.rglob('*.json'))
