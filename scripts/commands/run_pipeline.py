"""Internal entry point for one isolated pipeline job."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.pipeline_runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
