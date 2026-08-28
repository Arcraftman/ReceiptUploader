"""Human-friendly status view for all configured pipeline jobs."""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.pipeline_state import PipelineStateError, exclusive_job_lock


def main() -> int:
    state_paths = sorted((ROOT / "workspaces").glob("**/state.json"))
    if not state_paths:
        print("还没有任务状态。运行一次公司流程后会自动生成。")
        return 0
    print("账套 / 数据 / 月份 / 板块 | 模式 / 阶段 | 状态 / 当前步骤 | 最近更新")
    print("-" * 110)
    failed = 0
    for path in state_paths:
        try:
            state = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"状态损坏 | {path} | {exc}")
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
            f"{identity.get('loginAccount', 'default')}:{identity.get('accountbook', '?')} / {identity.get('dataset', '?')} / "
            f"{identity.get('month', '?')} / {identity.get('source', '?')} | "
            f"{state.get('mode', '?')} / {state.get('stage', '?')} | "
            f"{status} / {state.get('phase', '?')} | {state.get('updatedAt', '?')}"
        )
        if state.get("error"):
            print(f"  错误：{state['error']}")
    print(f"\n共 {len(state_paths)} 个任务状态，失败或损坏 {failed} 个。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
