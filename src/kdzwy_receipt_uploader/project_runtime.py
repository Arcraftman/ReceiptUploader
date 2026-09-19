"""Workspace selection and process boundaries, independent of source-checkout layout."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
from typing import Iterator

_active_root: ContextVar[Path | None] = ContextVar('kdzwy_project_root', default=None)


def project_root() -> Path:
    active = _active_root.get()
    if active is not None:
        return active
    configured = os.environ.get('KDZWY_PROJECT_ROOT')
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if not (candidate / 'config').is_dir():
            raise ValueError('Project workspace not found. KDZWY_PROJECT_ROOT must contain config/.')
        return candidate
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / 'config').is_dir():
            return candidate.resolve()
    source = Path(__file__).resolve().parents[2]
    if (source / 'pyproject.toml').is_file() and (source / 'config').is_dir():
        return source
    raise ValueError('Project workspace not found. Set KDZWY_PROJECT_ROOT to a directory containing config/.')


@contextmanager
def using_project(root: Path) -> Iterator[None]:
    """Bind the workspace for one invocation and restore it on every exit path."""
    token = _active_root.set(root.resolve())
    try:
        yield
    finally:
        _active_root.reset(token)


def process_environment(root: Path | None = None) -> dict[str, str]:
    """Propagate the workspace and make source and wheel workers equally importable."""
    package_parent = str(Path(__file__).resolve().parent.parent)
    path = os.pathsep.join(filter(None, [package_parent, os.environ.get('PYTHONPATH', '')]))
    return {**os.environ, 'KDZWY_PROJECT_ROOT': str(root or project_root()),
            'PYTHONPATH': path, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
