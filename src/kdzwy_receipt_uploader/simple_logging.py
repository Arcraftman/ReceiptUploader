"""Shared lightweight logging helpers for pipeline and upload scripts."""

from __future__ import annotations

import logging
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import TextIO


class _TranscriptStream:
    def __init__(self, original: TextIO, transcript: TextIO, lock: threading.Lock) -> None:
        self.original = original
        self.transcript = transcript
        self.lock = lock
        self._kdzwy_transcript = True

    def write(self, value: str) -> int:
        with self.lock:
            written = self.original.write(value)
            self.transcript.write(value)
            self.transcript.flush()
        return written

    def flush(self) -> None:
        with self.lock:
            self.original.flush()
            self.transcript.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.original, "isatty", lambda: False)())

    @property
    def encoding(self) -> str | None:
        return getattr(self.original, "encoding", None)


def install_console_transcript(log_dir: Path, name: str) -> Path:
    """Mirror all subsequent stdout/stderr output into one UTF-8 task transcript."""
    if getattr(sys.stdout, "_kdzwy_transcript", False):
        return Path(getattr(sys.stdout, "transcript_path", log_dir))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    preferred = Path(log_dir)
    fallback = Path(tempfile.gettempdir()) / "kdzwy_receipt_uploader" / "logs"
    transcript_path: Path | None = None
    transcript: TextIO | None = None
    for candidate in (preferred, fallback):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            transcript_path = candidate / f"{name}_{timestamp}.transcript.log"
            transcript = transcript_path.open("a", encoding="utf-8", buffering=1)
            break
        except OSError:
            continue
    if transcript is None or transcript_path is None:
        return fallback / f"{name}_{timestamp}.transcript.log"
    lock = threading.Lock()
    stdout = _TranscriptStream(sys.stdout, transcript, lock)
    stderr = _TranscriptStream(sys.stderr, transcript, lock)
    stdout.transcript_path = str(transcript_path)
    stderr.transcript_path = str(transcript_path)
    sys.stdout = stdout
    sys.stderr = stderr
    return transcript_path


def configure_pipeline_logger(log_dir: Path, name: str, to_console: bool = True) -> logging.Logger:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    preferred_log_dir = log_dir
    fallback_log_dir = Path(tempfile.gettempdir()) / "kdzwy_receipt_uploader" / "logs"
    file_path = None
    for candidate in (preferred_log_dir, fallback_log_dir):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            candidate_file = candidate / f"{name}_{timestamp}.log"
            test_handler = logging.FileHandler(candidate_file, encoding="utf-8")
            file_path = candidate_file
            file_handler = test_handler
            break
        except OSError:
            continue

    if file_path is None:
        file_handler = None
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    if file_handler is not None:
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    if to_console:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        logger.addHandler(console)

    if file_path is None and to_console:
        logger.warning("日志文件目录不可写，已降级为仅控制台日志。")

    return logger
