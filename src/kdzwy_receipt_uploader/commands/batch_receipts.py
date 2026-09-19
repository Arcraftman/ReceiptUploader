"""Stable CLI entry point for the standard Kdzwy receipt uploader project."""
from __future__ import annotations

import sys
from pathlib import Path

from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["--project-root", str(project_root()), *sys.argv[1:]]))
