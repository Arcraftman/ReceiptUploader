from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AppConfig:
    cookie_file: Path
    account_snapshot: Path
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
        runtime_dir = project_root / str(payload.get("runtime_dir", "runtime"))
        def resolve(value: str | None, default: Path) -> Path:
            if not value:
                return default
            candidate = Path(value)
            return candidate if candidate.is_absolute() else (project_root / candidate).resolve()
        return cls(
            cookie_file=resolve(payload.get("cookie_file"), runtime_dir / "accountbook.cookies.json"),
            account_snapshot=resolve(payload.get("account_snapshot"), runtime_dir / "accountbook_snapshot.json"),
            accounting_origin=str(payload.get("accounting_origin", "https://vip4-kj.kdzwy.com")).rstrip("/"),
            expected_company=str(payload["expected_company"]).strip() if payload.get("expected_company") else None,
            timeout_seconds=int(payload.get("timeout_seconds", 30)),
            upload_timeout_seconds=int(payload.get("upload_timeout_seconds", 60)),
            user_agent=str(payload.get("user_agent", "KdzwyReceiptUploader/0.1")),
        )
