from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.commands import create_company


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_explicit_base_template_creates_v3_company_config(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "templates" / "weiyu"
    source.mkdir(parents=True)
    write_json(source / "index.json", {"version": "5.0", "templatePattern": "*_template.json"})
    write_json(
        tmp_path / "runtime" / "registry" / "accountbooks.json",
        {"version": 2, "accountbooks": [{"key": "company_123", "name": "示例公司", "company_id": "123", "enabled": True}]},
    )
    write_json(
        tmp_path / "config" / "template_companies.json",
        {"version": 2, "template_companies": [{"key": "weiyu", "name": "微誉", "directory": "weiyu", "enabled": True}]},
    )
    (tmp_path / "config" / "companies").mkdir(parents=True)
    monkeypatch.setattr(create_company, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["create_company.py", "--name", "示例公司", "--base-template", "weiyu"])

    assert create_company.main() == 0

    company = json.loads((tmp_path / "config" / "companies" / "company_123_示例公司.json").read_text(encoding="utf-8"))
    assert company == {
        "version": 3,
        "company_key": "company_123",
        "company_id": "123",
        "company_name": "示例公司",
        "template_company": "company_123",
    }
    assert (tmp_path / "templates" / "company_123" / "index.json").is_file()


def test_existing_company_config_is_never_overwritten(tmp_path: Path, monkeypatch) -> None:
    write_json(tmp_path / "runtime" / "registry" / "accountbooks.json", {"accountbooks": [{"key": "company_123", "name": "示例公司", "company_id": "123"}]})
    existing = tmp_path / "config" / "companies" / "company_123_示例公司.json"
    write_json(existing, {"version": 3})
    monkeypatch.setattr(create_company, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["create_company.py", "--name", "示例公司"])
    try:
        create_company.main()
    except SystemExit as exc:
        assert "未覆盖" in str(exc)
    else:
        raise AssertionError("existing config must be protected")
