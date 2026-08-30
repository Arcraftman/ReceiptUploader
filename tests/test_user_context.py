from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.user_context import parse_system_context  # noqa: E402


def main() -> None:
    html = '''<script>var SYSTEM = {
        DBID: "8600001848820",
        UserName: "user@example",
        RealName: "测试用户",
        CURRENCY: "RMB"
    };</script>'''
    context = parse_system_context(html)
    assert context == {
        "userNo": "user@example",
        "userName": "测试用户",
        "dbid": "8600001848820",
    }
    print("User 上下文解析测试通过；date 未参与解析")


if __name__ == "__main__":
    main()
