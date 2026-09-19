"""Human-friendly status view for all configured pipeline jobs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.pipeline_state import PipelineStateError, exclusive_job_lock


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show the current status of all pipeline jobs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parse_args(argv)
    state_paths = sorted((project_root() / "workspaces").glob("**/state.json"))
    if not state_paths:
        print("No job state yet. Run a company workflow to create it.")
        return 0
    print("Target / Dataset / Month / Source - Stage - Status / Phase - Updated")
    print("-" * 110)
    failed = 0
    for path in state_paths:
        try:
            state = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Invalid state | {path} | {exc}")
            failed += 1
            continue
        identity = state.get("identity", {}) if isinstance(state.get("identity"), dict) else {}
        status = str(state.get("status", "unknown"))
        if status == "running":
            try:
                with exclusive_job_lock(path.with_name("job.lock")):
                    status = "interrupted"
            except PipelineStateError:
                pass
        if status in {"failed", "interrupted"}:
            failed += 1
        print(
            f"{identity.get('loginAccount', 'default')}:{identity.get('accountbook', '?')}"
            f"({identity.get('targetCompanyName') or identity.get('accountbookName', '?')}) / "
            f"{identity.get('sourceCompany', '?')}({identity.get('sourceCompanyName', '?')}) / "
            f"{identity.get('month', '?')} / {identity.get('source', '?')} | "
            f"{state.get('stage', '?')} | "
            f"{status} / {state.get('phase', '?')} | {state.get('updatedAt', '?')}"
        )
        if state.get("error"):
            print(f"  Error: {state['error']}")
    print(f"\nJobs: {len(state_paths)}; failed or invalid: {failed}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
