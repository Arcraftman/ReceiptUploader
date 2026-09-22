"""Local integration fixture; actual API output is reused without a second charge."""
import json
from pathlib import Path
from kdzwy_receipt_uploader.finance import serve
from kdzwy_receipt_uploader.finance.interpretation import validate_payload


def replay(root,payload):
    validate_payload(payload)
    (root/'request.json').write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    if (root/'fail.flag').exists():
        raise ValueError('测试：DeepSeek 暂时不可用')
    result = json.loads(Path('outputs/finance-ai-monitor/live-result.json').read_text(encoding='utf-8'))
    result['company'], result['month'] = payload['company'],payload['month']
    return result


serve.interpret = replay
serve.main(['--port','19337'])
