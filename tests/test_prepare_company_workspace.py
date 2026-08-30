from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.commands import prepare_company_workspace


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_workspace_always_contains_all_builtin_sources(tmp_path: Path, monkeypatch) -> None:
    company_id = "123"
    company_name = "测试公司"
    company_key = "company_123"
    config_name = f"company_{company_id}_{company_name}.json"
    data_root = f"data/inbox/company_{company_id}_{company_name}"
    company_config = tmp_path / "config" / "companies" / config_name

    write_json(
        tmp_path / "runtime" / "registry" / "accountbooks.json",
        {
            "version": 2,
            "accountbooks": [
                {
                    "key": company_key,
                    "name": company_name,
                    "company_id": company_id,
                    "login_account": "account_1",
                    "session_file": "http_sessions/test.json",
                    "enabled": True,
                },
                {
                    "key": "company_456",
                    "name": "目标账套公司",
                    "company_id": "456",
                    "login_account": "account_2",
                    "session_file": "http_sessions/target.json",
                    "enabled": True,
                }
            ],
        },
    )
    write_json(
        tmp_path / "config" / "template_companies.json",
        {
            "version": 2,
            "template_companies": [
                {"key": "weiyu", "name": "基础模板", "directory": "weiyu", "enabled": True}
            ],
        },
    )
    write_json(tmp_path / "templates" / "weiyu" / "index.json", {"version": 1, "templates": []})
    write_json(
        company_config,
        {
            "version": 3,
            "company_key": company_key,
            "company_id": company_id,
            "company_name": company_name,
            "template_company": "weiyu",
        },
    )
    september_project = tmp_path / data_root / "2026-09" / "project.json"
    write_json(
        september_project,
        {
            "version": 5,
            "company_key": company_key,
            "company_id": company_id,
            "company_name": company_name,
            "month": "2026-09",
            "target": {
                "accountbook_key": company_key,
                "company_id": company_id,
                "company_name": company_name,
            },
            "input": {
                "income_cost_filename": "收入成本表.xlsx",
                "usage_filename": "用途确认信息.xlsx",
                "usage_column": "E",
            },
            "defaults": {
                "mode": "analysis-only",
                "analysis_stage": "existing",
            },
            "sources": {
                "sales": {"enabled": True, "analysis_stage": "existing"},
                "purchase": {"enabled": False},
                "bank": {"enabled": False},
                "misc": {"enabled": False},
            },
        },
    )

    monkeypatch.setattr(prepare_company_workspace, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["prepare_company_workspace.py", "--config", str(company_config), "--month", "2026-09"],
    )
    assert prepare_company_workspace.main() == 0

    month_root = tmp_path / data_root / "2026-09"
    workspace_root = tmp_path / "workspaces" / "account_1" / company_key / "2026-09"
    for source in prepare_company_workspace.BUILT_IN_SOURCES:
        assert (month_root / "input" / source).is_dir()
        assert (workspace_root / "generated" / "maps" / source).is_dir()
        assert (workspace_root / "generated" / "receipts" / source).is_dir()
        assert (workspace_root / "generated" / "ocr" / source).is_dir()
        assert (workspace_root / "state" / source).is_dir()
        assert (workspace_root / "logs" / source).is_dir()

    project = json.loads((month_root / "project.json").read_text(encoding="utf-8"))
    assert project["version"] == 5
    assert project["target"]["accountbook_key"] == company_key
    assert set(project["sources"]) == set(prepare_company_workspace.BUILT_IN_SOURCES)
    assert project["sources"]["sales"]["enabled"] is True
    assert project["defaults"]["analysis_stage"] == "existing"
    assert "execution_enabled_sources" not in project

    marker = month_root / "input" / "sales" / "existing.pdf"
    marker.write_bytes(b"existing user input")
    assert prepare_company_workspace.main() == 0
    assert marker.read_bytes() == b"existing user input"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_company_workspace.py",
            "--config",
            str(company_config),
            "--month",
            "2026-10",
        ],
    )
    october_project = tmp_path / data_root / "2026-10" / "project.json"
    write_json(
        october_project,
        {
            "version": 5,
            "company_key": company_key,
            "company_id": company_id,
            "company_name": company_name,
            "month": "2026-10",
            "target": {
                "accountbook_key": "company_456",
                "company_id": "456",
                "company_name": "目标账套公司",
            },
            "input": {
                "income_cost_filename": "收入成本表.xlsx",
                "usage_filename": "用途确认信息.xlsx",
                "usage_column": "E",
            },
            "defaults": {"mode": "dry-run", "analysis_stage": "existing"},
            "sources": {
                "sales": {"enabled": False},
                "purchase": {"enabled": True},
                "bank": {"enabled": False},
                "misc": {"enabled": False},
            },
        },
    )
    assert prepare_company_workspace.main() == 0
    october = json.loads(october_project.read_text(encoding="utf-8"))
    assert october["defaults"]["mode"] == "dry-run"
    assert october["sources"]["purchase"]["enabled"] is True
    assert october["target"]["accountbook_key"] == "company_456"
    assert "workspace_directory" not in october
    assert (
        tmp_path / "workspaces" / "account_2" / "company_456" / f"from_{company_key}" / "2026-10"
    ).is_dir()
    unchanged_company = json.loads(company_config.read_text(encoding="utf-8"))
    assert not {"month", "defaults", "sources"} & set(unchanged_company)

    invalid = json.loads(october_project.read_text(encoding="utf-8"))
    invalid["sources"]["sales"]["enabled"] = "false"
    write_json(october_project, invalid)
    assert prepare_company_workspace.main() == 2
