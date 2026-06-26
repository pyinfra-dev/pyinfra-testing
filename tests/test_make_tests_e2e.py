"""End-to-end tests: drive make_fact_tests / make_operation_tests against the
sample fact and operations in tests/_fixtures.py, writing real case files and
running the generated unittest.TestCase.

These exercise the whole pipeline — metaclass discovery, command/requires_command
assertions, process() comparison, argument coercion, fact injection, noop and
mismatch reporting.
"""

import json
import unittest

from pyinfra_testing.facts import make_fact_tests
from pyinfra_testing.operations import make_operation_tests
from pyinfra_testing.util import FakeHost

from tests import _fixtures

BASE = "tests"


def write_cases(folder, cases):
    for name, data in cases.items():
        (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def run(testcase_cls):
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(testcase_cls)
    result = unittest.TestResult()
    suite.run(result)
    return result


# --- facts ------------------------------------------------------------------ #
def test_fact_case_passes(tmp_path):
    write_cases(tmp_path, {
        "ok": {
            "command": "echo sample",
            "requires_command": "echo",
            "output": ["42"],
            "fact": {"value": 42},
        },
    })
    result = run(make_fact_tests(BASE, "_fixtures.SampleFact", tmp_path))
    assert result.testsRun == 1
    assert result.wasSuccessful(), result.failures


def test_fact_mismatch_is_reported_as_failure(tmp_path):
    write_cases(tmp_path, {
        "bad": {
            "command": "echo sample",
            "requires_command": "echo",
            "output": ["42"],
            "fact": {"value": 999},  # wrong on purpose
        },
    })
    result = run(make_fact_tests(BASE, "_fixtures.SampleFact", tmp_path))
    assert not result.wasSuccessful()
    assert len(result.failures) == 1


def test_fact_wrong_command_is_reported(tmp_path):
    write_cases(tmp_path, {
        "badcmd": {
            "command": "echo WRONG",
            "requires_command": "echo",
            "output": ["1"],
            "fact": {"value": 1},
        },
    })
    result = run(make_fact_tests(BASE, "_fixtures.SampleFact", tmp_path))
    assert result.testsRun == 1
    assert not result.wasSuccessful()


# --- operations ------------------------------------------------------------- #
def test_operation_with_enum_and_dataclass_args(tmp_path):
    write_cases(tmp_path, {
        "create": {
            "args": ["host1"],
            "kwargs": {"color": "red", "opts": {"loud": True, "tags": ["a", "b"]}},
            "facts": {},
            "commands": ["echo host1 red --loud a,b"],
        },
    })
    result = run(make_operation_tests(BASE, "_fixtures.sample_op", tmp_path))
    assert result.testsRun == 1
    assert result.wasSuccessful(), result.failures or result.errors


def test_operation_noop(tmp_path):
    write_cases(tmp_path, {
        "absent": {
            "args": ["host1"],
            "kwargs": {"present": False},
            "facts": {},
            "commands": [],
            "noop_description": "host1 is absent",
        },
    })
    result = run(make_operation_tests(BASE, "_fixtures.sample_op", tmp_path))
    assert result.wasSuccessful(), result.failures or result.errors


def test_operation_reads_injected_fact(tmp_path):
    # fact_op calls host.get_fact(SampleFact, _sudo=True); the fact value is
    # injected by its canonical key and _sudo is stripped during lookup.
    fact_key = FakeHost._get_fact_key(_fixtures.SampleFact)
    write_cases(tmp_path, {
        "reads_fact": {
            "args": ["host1"],
            "facts": {fact_key: {"value": 7}},
            "commands": ["echo host1 7"],
        },
    })
    result = run(make_operation_tests(BASE, "_fixtures.fact_op", tmp_path))
    assert result.wasSuccessful(), result.failures or result.errors


def test_operation_command_mismatch_is_reported(tmp_path):
    write_cases(tmp_path, {
        "wrong": {
            "args": ["host1"],
            "kwargs": {},
            "facts": {},
            "commands": ["echo WRONG"],
        },
    })
    result = run(make_operation_tests(BASE, "_fixtures.sample_op", tmp_path))
    assert result.testsRun == 1
    assert not result.wasSuccessful()
