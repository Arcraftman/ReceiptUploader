from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AppConfig:
    cookie_file: Path
    accounting_origin: str = "https://vip4-kj.kdzwy.com"
    expected_company: str | None = None
    timeout_seconds: int = 30
    upload_timeout_seconds: int = 60
    user_agent: str = "KdzwyReceiptUploader/0.1"

    @classmethod
    def from_json(cls, path: Path, project_root: Path) -> "AppConfig":
        payload: dict[str, Any] = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"应用配置必须是 JSON 对象：{path}")
        if payload.get("version") != 2:
            raise ValueError(f"应用配置版本必须为 2：{path}")
        allowed = {
            "version",
            "accounting_origin",
            "cookie_file",
            "expected_company",
            "timeout_seconds",
            "upload_timeout_seconds",
            "user_agent",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"应用配置包含不支持的字段：{', '.join(unknown)}")
        def resolve(value: str | None, default: Path) -> Path:
            if not value:
                return default
            candidate = Path(value)
            return candidate if candidate.is_absolute() else (project_root / candidate).resolve()
        return cls(
            cookie_file=resolve(payload.get("cookie_file"), project_root / "runtime" / "accountbook.cookies.json"),
            accounting_origin=str(payload.get("accounting_origin", "https://vip4-kj.kdzwy.com")).rstrip("/"),
            expected_company=str(payload["expected_company"]).strip() if payload.get("expected_company") else None,
            timeout_seconds=int(payload.get("timeout_seconds", 30)),
            upload_timeout_seconds=int(payload.get("upload_timeout_seconds", 60)),
            user_agent=str(payload.get("user_agent", "KdzwyReceiptUploader/0.1")),
        )
