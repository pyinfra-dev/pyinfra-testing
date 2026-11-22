"""Exercise log assertions through generated unittest cases."""

import logging

import pytest
import yaml
from pyinfra import logger

from pyinfra_testing.facts import make_fact_tests
from pyinfra_testing.logs import assert_logs, capture_logs
from pyinfra_testing.operations import make_operation_tests

from tests.test_make_tests_e2e import run, write_cases


def logging_case(**overrides):
    return {
        "args": ["test-value"],
        "commands": ["echo test-value"],
        "expected_logs": {
            "contains": ["value=test-value"],
            "excludes": ["not-logged"],
        },
        **overrides,
    }


def test_operation_log_expectations_pass(tmp_path):
    write_cases(tmp_path, {"logs": logging_case()})
    result = run(make_operation_tests("tests", "_fixtures.logging_op", tmp_path))
    assert result.testsRun == 1
    assert result.wasSuccessful(), result.failures or result.errors


def test_forbidden_string_in_formatted_message_fails(tmp_path):
    write_cases(
        tmp_path,
        {
            "forbidden": logging_case(expected_logs={"excludes": ["test-value"]}),
        },
    )
    result = run(make_operation_tests("tests", "_fixtures.logging_op", tmp_path))
    assert len(result.failures) == 1
    failure = result.failures[0][1]
    assert "Unexpected substring in pyinfra logs: 'test-value'" in failure
    assert "Captured logs:\nvalue=test-value" in failure
    assert not result.errors


def test_missing_log_failure_shows_expected_string_and_captured_logs(tmp_path):
    write_cases(
        tmp_path,
        {
            "missing": logging_case(expected_logs={"contains": ["Missing message"]}),
        },
    )
    result = run(make_operation_tests("tests", "_fixtures.logging_op", tmp_path))
    assert len(result.failures) == 1
    failure = result.failures[0][1]
    assert "Expected substring missing from pyinfra logs: 'Missing message'" in failure
    assert "Captured logs:\nvalue=test-value" in failure
    assert not result.errors


def test_forbidden_string_in_logged_traceback_fails():
    with capture_logs() as logs:
        try:
            raise ValueError("test-value")
        except ValueError:
            logger.exception("Operation failed")

    with pytest.raises(AssertionError, match="Unexpected substring") as error:
        assert_logs(logs.getvalue(), {"excludes": ["test-value"]})
    assert "ValueError: test-value" in str(error.value)


@pytest.mark.parametrize(
    "expected, succeeds",
    [
        ({"excludes": ["s3cr3t"]}, True),
        ({"contains": ["missing log"]}, False),
    ],
)
def test_silent_operation(tmp_path, expected, succeeds):
    write_cases(
        tmp_path,
        {
            "silent": {
                "args": ["hello"],
                "commands": ["echo hello"],
                "expected_logs": expected,
            },
        },
    )
    result = run(make_operation_tests("tests", "_fixtures.sample_op", tmp_path))
    assert result.wasSuccessful() is succeeds
    assert not result.errors


@pytest.mark.parametrize("forbidden, succeeds", [("s3cr3t", True), ("42", False)])
def test_fact_logs(tmp_path, forbidden, succeeds):
    write_cases(
        tmp_path,
        {
            "fact": {
                "command": "echo sample",
                "requires_command": "echo",
                "output": ["42"],
                "fact": {"value": 42},
                "expected_logs": {
                    "contains": ["Processing value 42"],
                    "excludes": [forbidden],
                },
            },
        },
    )
    result = run(make_fact_tests("tests", "_fixtures.LoggingFact", tmp_path))
    assert result.wasSuccessful() is succeeds
    assert not result.errors


def test_config_is_scoped_to_each_case(tmp_path):
    write_cases(
        tmp_path,
        {
            "diff": {"config": {"DIFF": True}, "commands": ["echo / True"]},
            "no_diff": {"commands": ["echo / False"]},
        },
    )
    result = run(make_operation_tests("tests", "_fixtures.stateful_op", tmp_path))
    assert result.testsRun == 2
    assert result.wasSuccessful(), result.failures or result.errors


def test_platform_skip_does_not_require_logs(tmp_path):
    write_cases(
        tmp_path, {"skip": logging_case(require_platform=["unsupported-platform"])}
    )
    result = run(make_operation_tests("tests", "_fixtures.logging_op", tmp_path))
    assert result.wasSuccessful()
    assert len(result.skipped) == 1


@pytest.mark.parametrize(
    "expected",
    [
        {"contains": ["test-value"]},
        {"excludes": ["test-value"]},
    ],
)
def test_logger_settings_restored_even_on_failure(tmp_path, expected):
    write_cases(tmp_path, {"restore": logging_case(expected_logs=expected)})
    old_level = logger.level
    handlers = logger.handlers[:]
    logger.setLevel(logging.ERROR)
    try:
        run(make_operation_tests("tests", "_fixtures.logging_op", tmp_path))
        assert logger.level == logging.ERROR
        assert logger.handlers == handlers
    finally:
        logger.setLevel(old_level)


def test_log_capture_restores_settings_after_exception():
    old_level = logger.level
    handlers = logger.handlers[:]
    with pytest.raises(ValueError, match="test error"):
        with capture_logs():
            raise ValueError("test error")
    assert logger.level == old_level
    assert logger.handlers == handlers


@pytest.mark.parametrize(
    "expected",
    [
        {"exclude": ["s3cr3t"]},
        {"excludes": "s3cr3t"},
        {"contains": [42]},
        ["s3cr3t"],
    ],
)
def test_invalid_expectations_are_rejected(tmp_path, expected):
    write_cases(tmp_path, {"invalid": logging_case(expected_logs=expected)})
    result = run(make_operation_tests("tests", "_fixtures.logging_op", tmp_path))
    assert len(result.errors) == 1
    assert "ValueError: expected_logs" in result.errors[0][1]


@pytest.mark.parametrize("updating", [False, True])
def test_template_diff_logs_from_yaml_case(tmp_path, updating):
    facts = {
        "files.File": {"path=/etc/config": None},
        "files.Directory": {"path=/etc/config": None},
    }
    contains = ["Will create", "setting=public"]
    if updating:
        facts.update(
            {
                "files.File": {
                    "path=/etc/config": {"mode": 644, "user": "root", "group": "root"}
                },
                "files.Sha1File": {"path=/etc/config": "different"},
                "files.FileContents": {"path=/etc/config": ["setting=old"]},
            }
        )
        contains = ["Will modify", "setting=old", "setting=public"]
    case = {
        "args": ["io:setting={{ value }}", "/etc/config"],
        "kwargs": {"create_remote_dir": False, "value": "public"},
        "config": {"DIFF": True},
        "facts": facts,
        "commands": [["upload", "setting=public", "/etc/config"]],
        "expected_logs": {"contains": contains, "excludes": ["s3cr3t"]},
    }
    (tmp_path / "template.yaml").write_text(yaml.safe_dump(case), encoding="utf-8")
    result = run(make_operation_tests("pyinfra.operations", "files.template", tmp_path))
    assert result.testsRun == 1
    assert result.wasSuccessful(), result.failures or result.errors
