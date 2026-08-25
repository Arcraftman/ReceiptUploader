from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    data: Path
    inbox: Path
    processing: Path
    submitted: Path
    failed: Path
    logs: Path
    config: Path
    schema: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        root = root.resolve()
        data = root / "data"
        runtime = root / "runtime"
        return cls(
            root=root,
            data=data,
            inbox=data / "inbox",
            processing=runtime / "processing",
            submitted=runtime / "submitted",
            failed=runtime / "failed",
            logs=runtime / "logs",
            config=root / "config",
            schema=root / "schema",
        )

    def ensure(self) -> None:
        for path in (
            self.inbox,
            self.processing,
            self.submitted,
            self.failed,
            self.logs,
            self.config,
            self.schema,
        ):
            path.mkdir(parents=True, exist_ok=True)
