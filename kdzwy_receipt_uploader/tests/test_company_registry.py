from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kdzwy_receipt_uploader.company_registry import (
    AccountbookProfile,
    CompanyJob,
    CompanyRegistryError,
    DatasetProfile,
    build_job_settings,
    validate_accountbook_session,
)


class RegistryTests(unittest.TestCase):
    def test_dataset_identity_and_accountbook_identity_are_separate(self) -> None:
        defaults = {"paths": {"month_dir": "data/inbox/{company}/{month}", "map_file": "data/inbox/{company}/{month}/maps/map.json"}}
        accountbook = AccountbookProfile("target", "目标账套", "../http_sessions/target.json")
        dataset = DatasetProfile("source", "资料法定主体", "data/inbox/source", pipeline_overrides={"only_mapped_invoices": True})
        job = CompanyJob("target", "source", "8月", "analysis-only", "sales", "test", True)
        settings = build_job_settings(defaults, accountbook, dataset, job)
        self.assertEqual(settings["accountbook_name"], "目标账套")
        self.assertEqual(settings["document_entity_name"], "资料法定主体")
        self.assertEqual(settings["company"], "source")
        self.assertTrue(settings["cross_entity"])
        self.assertEqual(settings["paths"]["map_file"], "data/inbox/source/{month}/maps/map.json")

    def test_accountbook_session_must_match_target_not_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.json"
            session.write_text(json.dumps({"company_name": "目标账套", "cookies": [{}], "access_token": "token"}), encoding="utf-8")
            accountbook = AccountbookProfile("target", "目标账套", str(session))
            self.assertEqual(validate_accountbook_session(root, accountbook), session)
            wrong = AccountbookProfile("wrong", "其他账套", str(session))
            with self.assertRaises(CompanyRegistryError):
                validate_accountbook_session(root, wrong)


if __name__ == "__main__":
    unittest.main()
