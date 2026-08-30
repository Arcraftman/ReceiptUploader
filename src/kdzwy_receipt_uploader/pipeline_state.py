"""Durable, observable state for one accountbook/source-company/month/source job.

The state store is deliberately not an upload idempotency mechanism.  It records
what happened, while business artifacts and live read-back remain authoritative.
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = "1.0"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PipelineStateError(RuntimeError):
    pass


class PipelineStateStore:
    """Atomically maintain the latest job state plus an append-only event log."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.events_path = self.path.with_name("events.jsonl")

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineStateError(f"任务状态不可读取：{self.path}：{exc}") from exc
        if not isinstance(payload, dict):
            raise PipelineStateError(f"任务状态必须是JSON对象：{self.path}")
        return payload

    def begin(self, identity: Mapping[str, Any], *, mode: str, stage: str) -> dict[str, Any]:
        previous = self.load()
        if previous.get("status") == "running":
            abandoned = dict(previous)
            abandoned.update({
                "status": "abandoned",
                "updatedAt": utc_now(),
                "finishedAt": utc_now(),
                "error": "previous process exited without a terminal state",
            })
            self._append_event(abandoned, event="run_abandoned")
        attempt = int(previous.get("attempt", 0) or 0) + 1
        state = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": uuid.uuid4().hex,
            "attempt": attempt,
            "identity": dict(identity),
            "mode": mode,
            "stage": stage,
            "status": "running",
            "phase": "orchestration",
            "startedAt": utc_now(),
            "updatedAt": utc_now(),
            "finishedAt": None,
            "exitCode": None,
            "error": None,
            "artifacts": {},
            "counters": {},
        }
        self._commit(state, event="run_started")
        return state

    def update(
        self,
        *,
        status: str | None = None,
        phase: str | None = None,
        artifacts: Mapping[str, Any] | None = None,
        counters: Mapping[str, Any] | None = None,
        error: str | None = None,
        exit_code: int | None = None,
        event: str = "state_updated",
    ) -> dict[str, Any]:
        state = self.load()
        if not state:
            raise PipelineStateError(f"任务状态尚未初始化：{self.path}")
        if status is not None:
            state["status"] = status
        if phase is not None:
            state["phase"] = phase
        if artifacts:
            state.setdefault("artifacts", {}).update(dict(artifacts))
        if counters:
            state.setdefault("counters", {}).update(dict(counters))
        if error is not None:
            state["error"] = error
        if exit_code is not None:
            state["exitCode"] = int(exit_code)
        state["updatedAt"] = utc_now()
        if state.get("status") in TERMINAL_STATUSES:
            state["finishedAt"] = utc_now()
        self._commit(state, event=event)
        return state

    def _commit(self, state: Mapping[str, Any], *, event: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        self._append_event(state, event=event)

    def _append_event(self, state: Mapping[str, Any], *, event: str) -> None:
        event_payload = {
            "at": utc_now(),
            "event": event,
            "runId": state.get("runId"),
            "attempt": state.get("attempt"),
            "status": state.get("status"),
            "phase": state.get("phase"),
            "exitCode": state.get("exitCode"),
            "error": state.get("error"),
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(json.dumps(event_payload, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


@contextmanager
def exclusive_job_lock(path: Path) -> Iterator[None]:
    """Use an OS file lock so a crashed process releases ownership automatically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    stream.seek(0)
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise PipelineStateError(f"同一任务已有进程运行：{path}") from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise PipelineStateError(f"同一任务已有进程运行：{path}") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        stream.close()
