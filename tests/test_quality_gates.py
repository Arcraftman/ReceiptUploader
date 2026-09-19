"""A missing module or uncovered branch must fail the quality gate itself."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('coverage_gate', ROOT/'scripts/maintenance/check_coverage.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_gate_rejects_missing_modules_and_functions():
    for policy in [{'file':'missing.py','line':90,'branch':90},
                   {'file':'exists.py','function':'removed','line':90,'branch':90}]:
        assert gate.check({'files':{'exists.py':{}}}, {'gates':[policy]})


def test_gate_measures_branches_separately():
    report = {'files':{'src\\module.py':{'summary':{
        'covered_lines':10,'num_statements':10,'covered_branches':1,'num_branches':2}}}}
    assert gate.check(report, {'gates':[{'file':'src/module.py','line':100,'branch':90}]})
    assert not gate.check(report, {'gates':[{'file':'src/module.py','line':100,'branch':50}]})
