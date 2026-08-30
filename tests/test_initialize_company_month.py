from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
COMMANDS = ROOT / "scripts" / "commands"
for candidate in (ROOT, SRC, COMMANDS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import initialize_company_month as month_initializer  # noqa: E402
from initialize_company_month import (  # noqa: E402
    BUILT_IN_SOURCES,
    choose_template,
    load_default_bank_exceptions,
    normalize_month,
    normalize_input_settings,
    normalize_source_settings,
    parse_args,
    resolve_target_accountbook_selector,
)
from kdzwy_receipt_uploader.company_registry import CompanyRegistryError  # noqa: E402


class InitializeCompanyMonthLegacyTests(unittest.TestCase):
    __test__ = False
    def test_month_is_normalized_and_requires_year(self) -> None:
        self.assertEqual(normalize_month("2026-08"), "2026-08")
        with self.assertRaises(CompanyRegistryError):
            normalize_month("8月")
        with self.assertRaises(CompanyRegistryError):
            normalize_month("2026-8")

    def test_new_dataset_uses_stable_company_identity(self) -> None:
        payload = {"version": 1, "datasets": []}
        updated, record, created = upsert_dataset(
            payload, default_dataset_key("17867515"), "17867515", "上海微誉信息技术有限公司"
        )
        self.assertTrue(created)
        self.assertEqual(record["key"], "company_17867515")
        self.assertEqual(
            record["data_root"],
            "data/inbox/company_17867515_上海微誉信息技术有限公司",
        )
        self.assertEqual(len(updated["datasets"]), 1)

    def test_built_in_sources_are_fixed(self) -> None:
        self.assertEqual(set(BUILT_IN_SOURCES), {"sales", "purchase", "misc", "bank"})
        self.assertEqual(len(BUILT_IN_SOURCES), 4)

        normalized = normalize_source_settings({})
        self.assertEqual(set(normalized), set(BUILT_IN_SOURCES))

    def test_source_settings_preserve_existing_enabled_and_overrides(self) -> None:
        existing = {
            "sales": {
                "enabled": True,
                "overrides": {"analysis_stage": "existing", "llm_workers": 1},
            },
            "purchase": {
                "enabled": False,
                "overrides": {"only_mapped_invoices": True},
            },
        }

        normalized = normalize_source_settings(existing)

        self.assertTrue(normalized["sales"]["enabled"])
        self.assertEqual(normalized["sales"]["overrides"], existing["sales"]["overrides"])
        self.assertFalse(normalized["purchase"]["enabled"])
        self.assertEqual(normalized["purchase"]["overrides"], existing["purchase"]["overrides"])

    def test_missing_sources_default_to_disabled(self) -> None:
        normalized = normalize_source_settings({"sales": {"enabled": True}})

        self.assertTrue(normalized["sales"]["enabled"])
        for source in set(BUILT_IN_SOURCES) - {"sales"}:
            self.assertFalse(normalized[source]["enabled"])

    def test_nonstandard_source_is_rejected(self) -> None:
        with self.assertRaises(CompanyRegistryError):
            normalize_source_settings({"sales": True, "legacy": False})
        with self.assertRaises(CompanyRegistryError):
            normalize_source_settings({"sales": True})
        with self.assertRaises(CompanyRegistryError):
            normalize_source_settings({"sales": {"enabled": "false"}})

    def test_month_cli_requires_dataset_month_and_target(self) -> None:
        with patch.object(sys, "argv", ["initialize_company_month.py", "company_1_测试公司", "2026-09"]):
            with self.assertRaises(SystemExit) as raised:
                parse_args()
        self.assertEqual(raised.exception.code, 2)

        with patch.object(sys, "argv", ["initialize_company_month.py", "company_1_测试公司", "2026-09", "company_2"]):
            args = parse_args()
        self.assertEqual(args.company_config_name, "company_1_测试公司")
        self.assertEqual(args.month, "2026-09")
        self.assertEqual(args.target_accountbook, "company_2")

        for obsolete in (["--sources", "sales"], ["--template-company", "weiyu"], ["--dataset-key", "demo"]):
            with self.subTest(obsolete=obsolete):
                with patch.object(
                    sys,
                    "argv",
                    ["initialize_company_month.py", "company_1_测试公司", "2026-09", *obsolete],
                ):
                    with self.assertRaises(SystemExit) as raised:
                        parse_args()
                self.assertEqual(raised.exception.code, 2)

    def test_target_selector_accepts_key_id_or_exact_name(self) -> None:
        from kdzwy_receipt_uploader.company_registry import AccountbookProfile

        target = AccountbookProfile("target", "目标公司", "session.json", company_id="2")
        accountbooks = {"target": target}
        for selector in ("target", "2", "目标公司"):
            with self.subTest(selector=selector):
                self.assertEqual(resolve_target_accountbook_selector(accountbooks, selector), target)

    def test_template_must_already_be_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "template_companies.json"
            registry.write_text(
                '{"template_companies":[{"key":"weiyu","name":"测试公司","directory":"weiyu","enabled":true}]}',
                encoding="utf-8",
            )
            with self.assertRaises(CompanyRegistryError):
                choose_template(
                    {"company_key": "company_1", "company_name": "测试公司", "template_company": ""},
                    registry,
                )

    def test_initializer_keeps_company_shared_and_months_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)

            def write(path: Path, value: object) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

            company_name = "测试公司"
            config_name = "company_1_测试公司.json"
            company_config = project_root / "config" / "companies" / config_name
            write(
                company_config,
                {
                    "version": 2,
                    "company_key": "company_1",
                    "company_id": "1",
                    "company_name": company_name,
                    "enabled": True,
                    "dataset": "company_1",
                    "template_company": "weiyu",
                },
            )
            write(
                project_root / "config" / "accountbooks.json",
                {
                    "accountbooks": [
                        {
                            "key": "company_1",
                            "name": company_name,
                            "company_id": "1",
                            "login_account": "account_1",
                            "session_file": "http_sessions/test.json",
                            "enabled": True,
                            "pipeline_overrides": {},
                        },
                        {
                            "key": "company_2",
                            "name": "目标公司",
                            "company_id": "2",
                            "login_account": "account_1",
                            "session_file": "http_sessions/target.json",
                            "enabled": True,
                            "pipeline_overrides": {},
                        }
                    ]
                },
            )
            write(
                project_root / "config" / "datasets.json",
                {
                    "datasets": [
                        {
                            "key": "company_1",
                            "company_id": "1",
                            "entity_name": company_name,
                            "enabled": True,
                            "data_root": "data/inbox/company_1_测试公司",
                            "pipeline_overrides": {},
                        }
                    ]
                },
            )
            write(
                project_root / "config" / "template_companies.json",
                {
                    "template_companies": [
                        {"key": "weiyu", "name": "基础模板", "directory": "weiyu", "enabled": True}
                    ]
                },
            )
            write(project_root / "templates" / "weiyu" / "index.json", {"templates": []})
            september_project = (
                project_root / "data" / "inbox" / "company_1_测试公司" / "2026-09" / "project.json"
            )
            write(
                september_project,
                {
                    "version": 4,
                    "company_key": "company_1",
                    "company_id": "1",
                    "company_name": company_name,
                    "dataset": "company_1",
                    "month": "2026-09",
                    "target": {
                        "accountbook_key": "company_1",
                        "company_id": "1",
                        "company_name": company_name,
                    },
                    "defaults": {"mode": "dry-run", "analysis_stage": "existing"},
                    "sources": {
                        "sales": {"enabled": True, "overrides": {"llm_workers": 1}},
                        "purchase": {"enabled": False},
                        "bank": {"enabled": False},
                        "misc": {"enabled": False},
                    },
                },
            )

            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with (
                patch.object(month_initializer, "ROOT", project_root),
                patch.object(month_initializer.subprocess, "run", return_value=completed),
                patch.object(sys, "argv", ["initialize_company_month.py", config_name, "2026-09"]),
            ):
                self.assertEqual(month_initializer.main(), 0)

            updated = json.loads(company_config.read_text(encoding="utf-8"))
            self.assertTrue(updated["enabled"])
            self.assertEqual(updated["template_company"], "weiyu")
            self.assertFalse({"month", "defaults", "sources"} & set(updated))
            september = json.loads(september_project.read_text(encoding="utf-8"))
            self.assertEqual(september["version"], 4)
            self.assertEqual(september["target"]["accountbook_key"], "company_1")
            self.assertEqual(september["defaults"]["mode"], "dry-run")
            self.assertEqual(set(september["sources"]), set(BUILT_IN_SOURCES))
            self.assertTrue(september["sources"]["sales"]["enabled"])
            self.assertEqual(
                september["sources"]["sales"]["overrides"],
                {"llm_workers": 1},
            )

            with (
                patch.object(month_initializer, "ROOT", project_root),
                patch.object(month_initializer.subprocess, "run", return_value=completed),
                patch.object(sys, "argv", ["initialize_company_month.py", config_name, "2026-10"]),
            ):
                self.assertEqual(month_initializer.main(), 0)

            october_project = (
                project_root / "data" / "inbox" / "company_1_测试公司" / "2026-10" / "project.json"
            )
            october = json.loads(october_project.read_text(encoding="utf-8"))
            self.assertEqual(october["target"]["company_name"], company_name)
            self.assertEqual(october["defaults"]["mode"], "analysis-only")
            self.assertEqual(october["defaults"]["analysis_stage"], "ocr")
            self.assertTrue(all(not october["sources"][source]["enabled"] for source in BUILT_IN_SOURCES))

            with (
                patch.object(month_initializer, "ROOT", project_root),
                patch.object(month_initializer.subprocess, "run", return_value=completed),
                patch.object(
                    sys,
                    "argv",
                    ["initialize_company_month.py", config_name, "2026-11", "company_2"],
                ),
            ):
                self.assertEqual(month_initializer.main(), 0)
            november = json.loads(
                (project_root / "data" / "inbox" / "company_1_测试公司" / "2026-11" / "project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                november["target"],
                {
                    "accountbook_key": "company_2",
                    "company_id": "2",
                    "company_name": "目标公司",
                },
            )

            december_project = (
                project_root / "data" / "inbox" / "company_1_测试公司" / "2026-12" / "project.json"
            )
            write(
                december_project,
                {
                    "version": 4,
                    "company_key": "company_1",
                    "company_id": "1",
                    "company_name": company_name,
                    "dataset": "company_1",
                    "month": "2026-12",
                    "defaults": {"mode": "analysis-only", "analysis_stage": "ocr"},
                    "sources": {
                        source: {"enabled": False}
                        for source in BUILT_IN_SOURCES
                    },
                },
            )
            with (
                patch.object(month_initializer, "ROOT", project_root),
                patch.object(month_initializer.subprocess, "run", return_value=completed),
                patch.object(sys, "argv", ["initialize_company_month.py", config_name, "2026-12"]),
            ):
                self.assertEqual(month_initializer.main(), 2)

            with (
                patch.object(month_initializer, "ROOT", project_root),
                patch.object(month_initializer.subprocess, "run", return_value=completed),
                patch.object(
                    sys,
                    "argv",
                    ["initialize_company_month.py", config_name, "2026-12", "company_2"],
                ),
            ):
                self.assertEqual(month_initializer.main(), 0)
            december = json.loads(december_project.read_text(encoding="utf-8"))
            self.assertEqual(december["target"]["accountbook_key"], "company_2")

if __name__ == "__main__":
    unittest.main()


class InitializeCompanyMonthV7Tests(unittest.TestCase):
    def test_new_month_defaults_are_explicit_and_sources_are_disabled(self) -> None:
        self.assertEqual(
            normalize_input_settings(None),
            {"income_cost_filename": "收入成本表.xlsx", "usage_filename": "用途确认信息.xlsx", "usage_column": "E"},
        )
        sources = normalize_source_settings(None)
        self.assertEqual(set(sources), set(BUILT_IN_SOURCES))
        expected_core = {
            "enabled": False,
            "mode": "analysis-only",
            "analysis_stage": "ocr",
            "preload_items": False,
        }
        self.assertTrue(all(
            sources[source] == expected_core
            for source in ("sales", "purchase", "misc")
        ))
        self.assertEqual(
            set(sources["bank"]),
            {*expected_core, "banks", "exceptions"},
        )
        self.assertEqual(sources["bank"]["banks"], {})
        self.assertEqual(
            sources["bank"]["exceptions"],
            load_default_bank_exceptions(),
        )

    def test_existing_month_exception_configuration_is_not_merged_with_new_defaults(self) -> None:
        custom = ["客户甲"]
        sources = normalize_source_settings(
            {"bank": {"exceptions": custom}},
            bank_exception_defaults=["默认供应商"],
        )

        self.assertEqual(sources["bank"]["exceptions"], custom)

    def test_initializer_writes_explicit_v7_dataset_target_and_sources_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write(path: Path, value: object) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

            config_name = "company_1_测试公司.json"
            write(root / "config" / "companies" / config_name, {
                "version": 3, "company_key": "company_1", "company_id": "1",
                "company_name": "测试公司", "template_company": "weiyu",
            })
            accountbooks = root / "runtime" / "registry" / "accountbooks.json"
            write(accountbooks, {"version": 2, "accountbooks": [{
                "key": "company_1", "name": "测试公司", "company_id": "1",
                "login_account": "account_1", "enabled": True, "session_file": "session.json",
            }]})
            write(root / "config" / "template_companies.json", {
                "version": 2, "default_base_template": "weiyu", "template_companies": [{
                    "key": "weiyu", "name": "微誉", "directory": "weiyu", "enabled": True,
                }],
            })
            write(
                root / "config" / "bank_exception.defaults.json",
                {
                    "version": 2,
                    "exceptions": ["TIPS电子缴税款业务待报解预算收入"],
                    "pdf_keywords": {
                        "TIPS电子缴税款业务待报解预算收入": [
                            "上海银行电子缴税付款凭证"
                        ]
                    },
                },
            )
            write(root / "templates" / "weiyu" / "index.json", {"version": "5.0"})
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with (
                patch.object(month_initializer, "ROOT", root),
                patch.object(month_initializer, "ACCOUNTBOOKS_PATH", accountbooks),
                patch.object(month_initializer.subprocess, "run", return_value=completed),
                patch.object(sys, "argv", ["initialize_company_month.py", config_name, "2026-10", "company_1"]),
            ):
                self.assertEqual(month_initializer.main(), 0)
            project = json.loads((root / "data" / "inbox" / "company_1_测试公司" / "2026-10" / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["version"], 7)
            self.assertEqual(project["dataset"], {
                "company_key": "company_1", "company_id": "1", "company_name": "测试公司",
            })
            self.assertEqual(project["target"], {
                "accountbook_key": "company_1", "company_id": "1", "company_name": "测试公司",
            })
            self.assertIn("input", project)
            self.assertTrue(all(
                set(project["sources"][source]) >= {
                    "enabled", "mode", "analysis_stage", "preload_items"
                }
                for source in BUILT_IN_SOURCES
            ))
            self.assertEqual(project["sources"]["bank"]["banks"], {})
            self.assertEqual(
                project["sources"]["bank"]["exceptions"],
                load_default_bank_exceptions(
                    root / "config" / "bank_exception.defaults.json"
                ),
            )
            self.assertFalse({"company_key", "company_id", "company_name", "company_config", "login_account", "workspace_directory"} & set(project))
