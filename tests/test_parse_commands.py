"""Tests for parse_commands — turning yielded pyinfra commands into the JSON
shapes compared against a test case's `commands`."""

from pyinfra.api import HiddenValue, QuoteString, StringCommand

from pyinfra_testing.operations import parse_commands


def test_string_command_becomes_plain_string():
    assert parse_commands([StringCommand("echo", "hi")]) == ["echo hi"]


def test_raw_string_is_wrapped_and_stripped():
    assert parse_commands(["  echo hi  "]) == ["echo hi"]


def test_multiple_commands_preserve_order():
    cmds = [StringCommand("pct", "destroy", "150"), StringCommand("pct", "create", "150")]
    assert parse_commands(cmds) == ["pct destroy 150", "pct create 150"]


def test_masked_hidden_value_yields_raw_and_masked():
    cmd = StringCommand("pveum", "--password", QuoteString(HiddenValue("s3cr3t")))
    (result,) = parse_commands([cmd])
    assert isinstance(result, dict)
    assert result["raw"] == "pveum --password s3cr3t"
    assert "'*MASKED*'" in result["masked"]
    assert "s3cr3t" not in result["masked"]
