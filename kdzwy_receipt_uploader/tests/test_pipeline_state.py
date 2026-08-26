from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kdzwy_receipt_uploader.pipeline_state import PipelineStateStore


class PipelineStateTests(unittest.TestCase):
    def test_state_is_atomic_and_attempts_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            store = PipelineStateStore(state_path)

            first = store.begin({"accountbook": "xinghai", "source": "bank"}, mode="analysis-only", stage="ocr")
            store.update(phase="bank_split_complete", counters={"bankReceiptCount": 12})
            store.update(status="succeeded", phase="complete", exit_code=0)
            second = store.begin({"accountbook": "xinghai", "source": "bank"}, mode="analysis-only", stage="ocr")

            payload = json.loads(state_path.read_text(encoding="utf-8"))
            events = [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(first["attempt"], 1)
            self.assertEqual(second["attempt"], 2)
            self.assertEqual(payload["attempt"], 2)
            self.assertEqual(payload["status"], "running")
            self.assertEqual([event["event"] for event in events], ["run_started", "state_updated", "state_updated", "run_started"])
            self.assertFalse(state_path.with_suffix(".json.tmp").exists())

    def test_terminal_state_records_failure_details(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PipelineStateStore(Path(directory) / "state.json")
            store.begin({"dataset": "weiyu", "source": "bank"}, mode="analysis-only", stage="ocr")
            state = store.update(status="failed", phase="bank_split", exit_code=2, error="unrecognized receipt")

            self.assertEqual(state["status"], "failed")
            self.assertTrue(state["finishedAt"])
            self.assertEqual(state["exitCode"], 2)
            self.assertEqual(state["error"], "unrecognized receipt")

    def test_new_attempt_marks_unfinished_previous_run_abandoned(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PipelineStateStore(Path(directory) / "state.json")
            store.begin({"source": "bank"}, mode="analysis-only", stage="ocr")
            store.begin({"source": "bank"}, mode="analysis-only", stage="ocr")

            events = [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event"] for event in events], ["run_started", "run_abandoned", "run_started"])
            self.assertEqual(store.load()["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
