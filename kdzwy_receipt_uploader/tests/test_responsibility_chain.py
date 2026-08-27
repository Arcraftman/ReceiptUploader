from __future__ import annotations

from pathlib import Path

import pytest

from kdzwy_receipt_uploader.responsibility_chain import (
    SourceKind,
    parse_sources,
    run_selected_sources_safe,
)


def test_parse_sources() -> None:
    assert parse_sources("sales") == [SourceKind.SALES]
    assert parse_sources("sales purchase") == [SourceKind.SALES, SourceKind.PURCHASE]
    assert parse_sources("all") == [SourceKind.SALES, SourceKind.PURCHASE, SourceKind.BANK, SourceKind.MISC]


def test_y_z_block_without_side_effects(tmp_path: Path) -> None:
    contexts = run_selected_sources_safe(tmp_path, "all")
    assert [item.source for item in contexts] == [SourceKind.SALES, SourceKind.PURCHASE, SourceKind.BANK, SourceKind.MISC]
    assert contexts[0].data["validationReady"] is True
    assert contexts[1].data["validationReady"] is True
    assert contexts[2].data["blocked"] is True
    assert contexts[3].data["blocked"] is True


def test_invalid_source() -> None:
    with pytest.raises(ValueError):
        parse_sources("q")
