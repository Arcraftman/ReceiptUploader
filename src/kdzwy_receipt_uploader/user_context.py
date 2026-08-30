"""Resolve user context from the authorized account-book page.

The bundled account-book JS does not expose a dedicated current-user JSON
endpoint for voucher creation. Instead, default.jsp renders SYSTEM.RealName,
SYSTEM.UserName, and SYSTEM.DBID into the authorized account-book page. This
module reads those page values without printing cookies or credentials.
"""
from __future__ import annotations

import re
from typing import Any

from .models import ApiError


_SYSTEM_STRING_FIELDS = {
    "UserName": "userNo",
    "RealName": "userName",
    "DBID": "dbid",
}


def _read_system_value(html: str, field: str) -> str:
    match = re.search(
        rf"(?:^|[\s,]){re.escape(field)}\s*:\s*([\"'])(.*?)\1\s*,?",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise ApiError(f"账簿页面未找到 SYSTEM.{field}")
    return match.group(2).strip()


def parse_system_context(html: str) -> dict[str, str]:
    """Parse the user/account values rendered in default.jsp."""
    if not html or "var SYSTEM" not in html:
        raise ApiError("账簿页面没有 SYSTEM 上下文")
    return {
        output_key: _read_system_value(html, field)
        for field, output_key in _SYSTEM_STRING_FIELDS.items()
    }


def resolve_current_user(api: Any) -> dict[str, str]:
    """Read current user context from the authorized account-book page."""
    html = api.get_text("/default.jsp")
    return parse_system_context(html)
