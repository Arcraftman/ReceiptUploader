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
        job = CompanyJob(
            "target", "source", "2026-08", cross_company_upload_enabled=True
        )
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


class RegistryV8Tests(unittest.TestCase):
    def _company(self) -> CompanyProfile:
        return CompanyProfile("company_1", "1", "测试公司", "weiyu", "data/inbox/company_1_测试公司")

    def _project(self) -> dict[str, object]:
        project = {
            "version": 8,
            "month": "2026-08",
            "dataset": {
                "company_key": "company_1",
                "company_id": "1",
                "company_name": "测试公司",
            },
            "target": {"accountbook_key": "company_1", "company_id": "1", "company_name": "测试公司"},
            "input": {"usage_filename": "用途确认信息.xlsx", "usage_column": "E"},
            "defaults": {"analysis_validation": "strict"},
            "sources": {
                source: {
                    "enabled": source == "sales",
                    "stage": "ocr",
                }
                for source in ("sales", "purchase", "bank", "misc")
            },
        }
        project["sources"]["bank"]["banks"] = {
            "testbank": {
                "enabled": False,
                "bank_account_number": "100201",
                "split": {
                    "parts_per_page": 2,
                    "filename_index_length": 8,
                    "filename_index_prefix": "T",
                },
                "remark_template_map": {},
            },
        }
        project["sources"]["bank"]["statement_columns"] = {
            "testbank": {
                "index_column": None,
                "bank_debit_column": None,
                "bank_credit_column": None,
                "counterparty_name_column": None,
                "remark_column": None,
            }
        }
        project["sources"]["bank"]["exceptions"] = []
        project["sources"]["purchase"]["usage_confirmation_enabled"] = True
        return project

    def test_v8_project_requires_explicit_dataset_target_and_source_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            path.write_text(json.dumps(self._project(), ensure_ascii=False), encoding="utf-8")
            jobs = load_company_jobs(path, self._company())
            self.assertEqual(len(jobs), 4)
            self.assertEqual([job.source for job in jobs if job.enabled], ["sales"])
            self.assertEqual(jobs[0].input_config["usage_column"], "E")
            self.assertTrue(all(job.stage == "ocr" for job in jobs))
            bank_job = next(job for job in jobs if job.source == "bank")
            purchase_job = next(job for job in jobs if job.source == "purchase")
            self.assertTrue(purchase_job.overrides["usage_confirmation_enabled"])
            self.assertEqual(
                bank_job.overrides["banks"]["testbank"]["statement_columns"],
                {
                    "index_column": None,
                    "bank_debit_column": None,
                    "bank_credit_column": None,
                    "counterparty_name_column": None,
                    "remark_column": None,
                },
            )

    def test_purchase_usage_confirmation_flag_is_strict_and_defaults_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload["sources"]["purchase"].pop("usage_confirmation_enabled")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            jobs = load_company_jobs(path, self._company())
            purchase = next(job for job in jobs if job.source == "purchase")
            self.assertTrue(purchase.overrides["usage_confirmation_enabled"])

            payload["sources"]["purchase"]["usage_confirmation_enabled"] = "false"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "usage_confirmation_enabled"):
                load_company_jobs(path, self._company())

    def test_cross_entity_defaults_false_and_reuses_dataset_input_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload["target"] = {
                "accountbook_key": "company_2",
                "company_id": "2",
                "company_name": "目标公司",
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            jobs = load_company_jobs(path, self._company())
            self.assertTrue(all(job.accountbook == "company_1" for job in jobs))
            self.assertTrue(all(job.target_company_id == "1" for job in jobs))
            self.assertTrue(all(job.target_company_name == "测试公司" for job in jobs))
            self.assertTrue(all(not job.cross_company_upload_enabled for job in jobs))
            same_company_settings = build_job_settings(
                {"analysis_validation": "strict"},
                AccountbookProfile(
                    "company_1", "测试公司", "session.json", company_id="1"
                ),
                DatasetProfile(
                    "company_1", "测试公司", "data/inbox/company_1_测试公司"
                ),
                next(job for job in jobs if job.source == "sales"),
            )
            self.assertFalse(same_company_settings["cross_entity"])
            self.assertEqual(
                same_company_settings["workspace_root"],
                "workspaces/default/company_1/2026-08",
            )

            payload["defaults"]["cross_company_upload_enabled"] = True
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            sales_job = next(
                job for job in load_company_jobs(path, self._company())
                if job.source == "sales"
            )
            self.assertTrue(sales_job.cross_company_upload_enabled)
            self.assertEqual(sales_job.accountbook, "company_2")
            self.assertEqual(sales_job.target_company_id, "2")
            settings = build_job_settings(
                {"analysis_validation": "strict"},
                AccountbookProfile(
                    "company_2", "目标公司", "session.json", company_id="2"
                ),
                DatasetProfile(
                    "company_1", "测试公司", "data/inbox/company_1_测试公司"
                ),
                sales_job,
            )
            self.assertTrue(settings["cross_entity"])
            self.assertEqual(
                settings["paths"]["input_dir"],
                "data/inbox/company_1_测试公司/{month}/input",
            )
            self.assertEqual(
                settings["workspace_root"],
                "workspaces/default/company_2/from_company_1/2026-08",
            )

            payload = self._project()
            payload["defaults"]["allow_cross_entity"] = True
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "allow_cross_entity"):
                load_company_jobs(path, self._company())

    def test_each_source_requires_enabled_and_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            for field in ("enabled", "stage"):
                payload = self._project()
                payload["sources"]["purchase"].pop(field)
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(
                    CompanyRegistryError,
                    rf"sources\.purchase 缺少精确运行字段：.*{field}",
                ):
                    load_company_jobs(path, self._company())

    def test_disabled_source_still_requires_valid_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload["sources"]["misc"]["stage"] = "guess"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(
                CompanyRegistryError, "stage 只支持"
            ):
                load_company_jobs(path, self._company())

    def test_unified_bank_config_requires_split_and_exactly_five_separate_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload["sources"]["bank"]["statement_columns"]["testbank"].pop(
                "bank_credit_column"
            )
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "必须精确包含五个列配置"):
                load_company_jobs(path, self._company())

            payload = self._project()
            payload["sources"]["bank"]["banks"]["testbank"].pop("split")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "必须同时包含 enabled、bank_account_number、split 和 remark_template_map"):
                load_company_jobs(path, self._company())

            payload = self._project()
            payload["sources"]["bank"]["banks"]["testbank"].pop(
                "bank_account_number"
            )
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "bank_account_number"):
                load_company_jobs(path, self._company())

            payload = self._project()
            payload["sources"]["bank"].pop("banks")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "缺少统一多银行配置 banks"):
                load_company_jobs(path, self._company())

            payload = self._project()
            payload["sources"]["bank"].pop("exceptions")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "缺少特殊对象名称配置 exceptions"):
                load_company_jobs(path, self._company())

            payload = self._project()
            payload["sources"]["bank"].pop("statement_columns")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "缺少独立银行流水列配置"):
                load_company_jobs(path, self._company())

    def test_bank_exceptions_are_unique_exact_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload["sources"]["bank"]["exceptions"] = ["供应商甲", "张三"]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            jobs = load_company_jobs(path, self._company())
            bank_job = next(job for job in jobs if job.source == "bank")
            self.assertEqual(bank_job.overrides["exceptions"], ["供应商甲", "张三"])

            payload["sources"]["bank"]["exceptions"] = ["供应商甲", "供应商甲"]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "包含重复名称"):
                load_company_jobs(path, self._company())

    def test_enabled_bank_requires_all_statement_columns_to_be_filled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload["sources"]["sales"]["enabled"] = False
            payload["sources"]["bank"]["enabled"] = True
            payload["sources"]["bank"]["banks"]["testbank"]["enabled"] = True
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(
                CompanyRegistryError,
                "enabled=true 时必须填写",
            ):
                load_company_jobs(path, self._company())

    def test_bank_source_requires_at_least_one_enabled_bank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload["sources"]["sales"]["enabled"] = False
            payload["sources"]["bank"]["enabled"] = True
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "至少启用一家银行"):
                load_company_jobs(path, self._company())

    def test_runtime_settings_exclude_disabled_banks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            bank_job = next(
                job for job in load_company_jobs(path, self._company())
                if job.source == "bank"
            )
            accountbook = AccountbookProfile(
                "company_1", "测试公司", "session.json", company_id="1"
            )
            dataset = DatasetProfile(
                "company_1", "测试公司", "data/inbox/company_1_测试公司"
            )
            settings = build_job_settings(
                {"analysis_validation": "strict"}, accountbook, dataset, bank_job
            )
            self.assertEqual(settings["banks"], {})

            payload["sources"]["bank"]["banks"]["testbank"]["enabled"] = True
            payload["sources"]["bank"]["statement_columns"]["testbank"] = {
                "index_column": "B",
                "bank_debit_column": "F",
                "bank_credit_column": "G",
                "counterparty_name_column": "H",
                "remark_column": "I",
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            bank_job = next(
                job for job in load_company_jobs(path, self._company())
                if job.source == "bank"
            )
            settings = build_job_settings(
                {"analysis_validation": "strict"}, accountbook, dataset, bank_job
            )
            self.assertEqual(set(settings["banks"]), {"testbank"})

    def test_missing_or_mismatched_dataset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            payload = self._project()
            payload.pop("dataset")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "project.dataset"):
                load_company_jobs(path, self._company())

            payload = self._project()
            payload["dataset"]["company_id"] = "2"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "dataset 与资料公司配置身份不一致"):
                load_company_jobs(path, self._company())

            payload = self._project()
            payload.pop("target")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CompanyRegistryError, "project.target"):
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
