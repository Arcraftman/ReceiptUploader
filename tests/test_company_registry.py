from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kdzwy_receipt_uploader.company_registry import (
    AccountbookProfile,
    CompanyJob,
    CompanyProfile,
    CompanyRegistryError,
    DatasetProfile,
    build_job_settings,
    company_config_filename,
    load_company_jobs,
    load_company_profile,
    resolve_target_accountbook,
    validate_accountbook_session,
    workspace_relative_path,
)


class LegacyRegistryTests(unittest.TestCase):
    __test__ = False
    def test_company_config_filename_uses_id_and_real_name(self) -> None:
        self.assertEqual(
            company_config_filename("20151038", "星海公司"),
            "company_20151038_星海公司.json",
        )
        self.assertEqual(
            company_config_filename("21726397", "智轻云(上海):科技有限公司"),
            "company_21726397_智轻云(上海)_科技有限公司.json",
        )
        with self.assertRaises(CompanyRegistryError):
            company_config_filename("", "星海公司")

    def test_workspace_path_avoids_duplicate_same_entity_layer(self) -> None:
        self.assertEqual(
            workspace_relative_path("account_1", "company_17867515", "company_17867515", "2026-08").as_posix(),
            "workspaces/account_1/company_17867515/2026-08",
        )
        self.assertEqual(
            workspace_relative_path("account_1", "target", "source", "2026-08").as_posix(),
            "workspaces/account_1/target/from_source/2026-08",
        )

    def test_dataset_identity_and_accountbook_identity_are_separate(self) -> None:
        defaults = {
            "paths": {
                "month_dir": "data/inbox/{company}/{month}",
                "map_file": "{workspace_root}/generated/maps/{source}/map.json",
            }
        }
        accountbook = AccountbookProfile("target", "目标账套", "http_sessions/target.json")
        dataset = DatasetProfile("source", "资料法定主体", "data/inbox/source", pipeline_overrides={"only_mapped_invoices": True})
        job = CompanyJob("target", "source", "2026-08", "analysis-only", "sales", "test", True)
        settings = build_job_settings(defaults, accountbook, dataset, job)
        self.assertEqual(settings["accountbook_name"], "目标账套")
        self.assertEqual(settings["document_entity_name"], "资料法定主体")
        self.assertEqual(settings["company"], "source")
        self.assertTrue(settings["cross_entity"])
        self.assertEqual(
            settings["paths"]["map_file"],
            "workspaces/default/target/from_source/2026-08/generated/maps/{source}/map.json",
        )

    def test_removed_workspace_template_is_rejected(self) -> None:
        defaults = {
            "paths": {
                "map_file": "workspaces/{login_account}/{accountbook}/{company}/{month}/generated/map.json"
            }
        }
        accountbook = AccountbookProfile("target", "目标账套", "session.json")
        dataset = DatasetProfile("source", "资料法定主体", "data/inbox/source")
        job = CompanyJob("target", "source", "2026-08", allow_cross_entity=True)
        with self.assertRaises(CompanyRegistryError):
            build_job_settings(defaults, accountbook, dataset, job)

    def test_removed_deepseek_stage_is_rejected(self) -> None:
        defaults = {"analysis_stage": "deepseek", "paths": {}}
        accountbook = AccountbookProfile("target", "目标账套", "session.json")
        dataset = DatasetProfile("target", "目标账套", "data/inbox/target")
        job = CompanyJob("target", "target", "2026-08")
        with self.assertRaises(CompanyRegistryError):
            build_job_settings(defaults, accountbook, dataset, job)

    def test_nonstandard_company_config_filename_is_rejected_even_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "xinghai.json"
            path.write_text(
                json.dumps({
                    "company_id": "20151038",
                    "company_name": "星海公司",
                    "enabled": False,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(CompanyRegistryError):
                load_company_profile(path)

    def test_v4_project_rejects_ambiguous_legacy_accountbook_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            path.write_text(
                json.dumps({
                    "version": 4,
                    "company_id": "1",
                    "company_name": "测试公司",
                    "company_key": "company_1",
                    "dataset": "company_1",
                    "month": "2026-08",
                    "accountbook": "company_1",
                    "target": {
                        "accountbook_key": "company_1",
                        "company_id": "1",
                        "company_name": "测试公司",
                    },
                    "defaults": {"mode": "analysis-only", "analysis_stage": "ocr"},
                    "sources": {
                        source: {"enabled": False}
                        for source in ("sales", "purchase", "bank", "misc")
                    },
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            company = CompanyProfile("company_1", "1", "测试公司", "company_1", "weiyu")
            with self.assertRaisesRegex(CompanyRegistryError, "含义不明确"):
                load_company_jobs(path, company)

    def test_company_config_rejects_month_execution_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "company_1_测试公司.json"
            path.write_text(
                json.dumps({
                    "company_id": "1",
                    "company_name": "测试公司",
                    "company_key": "company_1",
                    "enabled": True,
                    "dataset": "company_1",
                    "template_company": "weiyu",
                    "defaults": {"mode": "analysis-only"},
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(CompanyRegistryError):
                load_company_profile(path)

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop("defaults")
            payload["target"] = {"accountbook_key": "company_1"}
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(CompanyRegistryError):
                load_company_profile(path)

    def test_source_enabled_must_be_json_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 4,
                        "company_id": "1",
                        "company_name": "测试公司",
                        "company_key": "company_1",
                        "dataset": "company_1",
                        "month": "2026-08",
                        "target": {
                            "accountbook_key": "company_1",
                            "company_id": "1",
                            "company_name": "测试公司",
                        },
                        "defaults": {"mode": "analysis-only", "analysis_stage": "ocr"},
                        "sources": {
                            "sales": {"enabled": "false"},
                            "purchase": {"enabled": False},
                            "bank": {"enabled": False},
                            "misc": {"enabled": False},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            company = CompanyProfile("company_1", "1", "测试公司", "company_1", "weiyu")
            with self.assertRaises(CompanyRegistryError):
                load_company_jobs(path, company)

    def test_template_is_loaded_only_from_company_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            path.write_text(
                json.dumps({
                    "version": 4,
                    "company_id": "1",
                    "company_name": "测试公司",
                    "company_key": "company_1",
                    "dataset": "company_1",
                    "month": "2026-08",
                    "target": {
                        "accountbook_key": "company_1",
                        "company_id": "1",
                        "company_name": "测试公司",
                    },
                    "template_company": "other",
                    "defaults": {"mode": "analysis-only", "analysis_stage": "ocr"},
                    "sources": {source: {"enabled": source == "sales"} for source in ("sales", "purchase", "bank", "misc")},
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            company = CompanyProfile("company_1", "1", "测试公司", "company_1", "weiyu")
            with self.assertRaises(CompanyRegistryError):
                load_company_jobs(path, company)

    def test_version_3_project_without_explicit_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            path.write_text(
                json.dumps({
                    "version": 3,
                    "company_id": "1",
                    "company_name": "测试公司",
                    "company_key": "company_1",
                    "dataset": "company_1",
                    "month": "2026-08",
                    "defaults": {"mode": "analysis-only", "analysis_stage": "ocr"},
                    "sources": {source: {"enabled": source == "sales"} for source in ("sales", "purchase", "bank", "misc")},
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            company = CompanyProfile("company_1", "1", "测试公司", "company_1", "weiyu")
            with self.assertRaisesRegex(CompanyRegistryError, "版本必须为 4"):
                load_company_jobs(path, company)

    def test_explicit_target_must_match_accountbook_registry(self) -> None:
        job = CompanyJob(
            "target",
            "source",
            "2026-08",
            target_company_id="2",
            target_company_name="目标账套",
        )
        target = AccountbookProfile("target", "目标账套", "session.json", company_id="2")
        self.assertEqual(resolve_target_accountbook(job, {"target": target}), target)
        with self.assertRaisesRegex(CompanyRegistryError, "company_id 不一致"):
            resolve_target_accountbook(job, {"target": AccountbookProfile("target", "目标账套", "session.json", company_id="3")})

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


class RegistryV5Tests(unittest.TestCase):
    def _company(self) -> CompanyProfile:
        return CompanyProfile("company_1", "1", "测试公司", "weiyu", "data/inbox/company_1_测试公司")

    def _project(self) -> dict[str, object]:
        return {
            "version": 5,
            "company_key": "company_1",
            "company_id": "1",
            "company_name": "测试公司",
            "month": "2026-08",
            "target": {"accountbook_key": "company_1", "company_id": "1", "company_name": "测试公司"},
            "input": {"income_cost_filename": "收入成本表.xlsx", "usage_filename": "用途确认信息.xlsx", "usage_column": "E"},
            "defaults": {"mode": "analysis-only", "analysis_stage": "ocr", "analysis_validation": "strict"},
            "sources": {source: {"enabled": source == "sales"} for source in ("sales", "purchase", "bank", "misc")},
        }

    def test_v5_project_loads_without_dataset_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            path.write_text(json.dumps(self._project(), ensure_ascii=False), encoding="utf-8")
            jobs = load_company_jobs(path, self._company())
            self.assertEqual(len(jobs), 4)
            self.assertEqual([job.source for job in jobs if job.enabled], ["sales"])
            self.assertEqual(jobs[0].input_config["usage_column"], "E")

    def test_unknown_legacy_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload["dataset"] = "company_1"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "不支持的字段"):
                load_company_jobs(path, self._company())

    def test_company_v3_has_only_identity_and_shared_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "company_1_测试公司.json"
            path.write_text(json.dumps({
                "version": 3, "company_key": "company_1", "company_id": "1",
                "company_name": "测试公司", "template_company": "weiyu",
            }, ensure_ascii=False), encoding="utf-8")
            company = load_company_profile(path)
            self.assertEqual(company.data_root, "data/inbox/company_1_测试公司")
