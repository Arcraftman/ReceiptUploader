"""Typed CLI options and stage notifications; serialized formats stay unchanged."""
from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True)
class PipelineOptions:
    run_config: Path
    app_config: Path
    limit: int = 0
    receipt_id: str = ''
    test_upload: bool = False
    state_file: Path | None = None
    concise: bool = False

    @classmethod
    def from_namespace(cls, args: Namespace) -> PipelineOptions:
        return cls(Path(args.run_config), Path(args.app_config), int(args.limit),
                   str(args.receipt_id), bool(args.test_upload),
                   Path(args.state_file) if args.state_file is not None else None,
                   bool(args.concise))


class StageCheckpoint(Protocol):
    def __call__(self, phase: str, *, artifacts: Mapping[str, str] | None = None,
                 counters: Mapping[str, int] | None = None,
                 event: str = 'phase_changed') -> None: ...
