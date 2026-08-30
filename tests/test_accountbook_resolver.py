from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kdzwy_receipt_uploader.accountbook_resolver import resolve_defaults


class FakeReadOnlyApi:
    def get_voucher_groups_v1(self):
        return [{"id": 123, "name": "记"}]

    def get_subject_tree(self, *, effective: int, expand: bool):
        assert effective == 0
        assert expand is True
        return {"rows": [
            {"id": 456, "number": "560203", "fullName": "管理费用_办公用品费"},
            {"id": 789, "number": "100201", "fullName": "银行存款_0301招商银行"},
        ]}


def test_resolve_ids_from_current_book():
    result = resolve_defaults(FakeReadOnlyApi(), {
        "voucher_defaults": {"group_name": "记"},
        "entry_defaults": [
            {"line_no": 1, "dc": 1, "account_number": "560203"},
            {"line_no": 2, "dc": -1, "account_number": "100201"},
        ],
    })
    assert result["voucher_defaults"]["group_id"] == "123"
    assert result["entry_defaults"][0]["account_id"] == "456"
    assert result["entry_defaults"][1]["account_id"] == "789"


if __name__ == "__main__":
    test_resolve_ids_from_current_book()
    print("动态 groupId/accountId 测试通过")
