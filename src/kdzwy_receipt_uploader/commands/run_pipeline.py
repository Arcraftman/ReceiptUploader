"""Internal entry point for one isolated pipeline job."""
from __future__ import annotations

import sys
from pathlib import Path

from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.pipeline_runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
