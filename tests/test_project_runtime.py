"""Workspace context and installed command boundaries."""
import ast
from pathlib import Path
import subprocess
import sys

import pytest

from kdzwy_receipt_uploader import command_dispatch
from kdzwy_receipt_uploader.project_runtime import project_root, using_project, process_environment

ROOT = Path(__file__).resolve().parents[1]


def test_project_context_restores_on_failure(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    monkeypatch.setenv('KDZWY_PROJECT_ROOT', str(tmp_path))
    outer = tmp_path / 'outer'
    inner = tmp_path / 'inner'
    with using_project(outer):
        assert project_root() == outer
        with pytest.raises(RuntimeError):
            with using_project(inner):
                assert project_root() == inner
                raise RuntimeError('stop')
        assert project_root() == outer
    assert project_root() == tmp_path


def test_worker_receives_bound_workspace_outside_checkout(tmp_path):
    workspace = tmp_path / 'workspace with spaces'
    (workspace / 'config').mkdir(parents=True)
    with using_project(workspace):
        result = subprocess.run(
            [sys.executable, '-m', 'kdzwy_receipt_uploader.commands.pipeline_status'],
            cwd=tmp_path, env=process_environment(), capture_output=True, text=True, timeout=15,
        )
    assert result.returncode == 0, result.stderr
    assert 'No job state yet' in result.stdout
    assert not (workspace / 'scripts').exists()


def test_direct_command_dispatch_does_not_spawn_process(monkeypatch, tmp_path):
    from kdzwy_receipt_uploader.commands import pipeline_status
    seen = []
    monkeypatch.setattr(pipeline_status, 'main', lambda argv: seen.append((argv, project_root())) or 0)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('Unexpected subprocess'))
    with using_project(tmp_path):
        assert command_dispatch.main(['status']) == 0
    assert seen == [([], tmp_path)]


def test_package_does_not_depend_on_checkout_scripts():
    package = ROOT / 'src/kdzwy_receipt_uploader'
    assert not (ROOT / 'scripts/commands').exists()
    for path in package.rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        assert 'sys.path.insert' not in source, path
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert 'scripts/commands/' not in node.value, path
                assert 'scripts/finance/serve.py' not in node.value, path
