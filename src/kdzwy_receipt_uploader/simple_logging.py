"""Shared lightweight logging helpers for pipeline and upload scripts."""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path


def configure_pipeline_logger(log_dir: Path, name: str, to_console: bool = True) -> logging.Logger:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
