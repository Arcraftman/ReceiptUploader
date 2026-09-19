"""Export a live read-only finance snapshot for diagnostics/workbook generation."""
import argparse
import json
import os
from pathlib import Path
import sys

from kdzwy_receipt_uploader.project_runtime import project_root
from kdzwy_receipt_uploader.finance.snapshot import collect_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company")
    parser.add_argument("month")
    args = parser.parse_args(argv)
    snapshot = collect_snapshot(project_root(), args.company, args.month)
    output = project_root() / "runtime/finance" / args.company / args.month
    output.mkdir(parents=True, exist_ok=True)
    target = output / "snapshot.json"
    fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    print(target)


if __name__ == "__main__":
    main()
