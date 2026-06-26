"""Tests for parse_value — the JSON -> richer-Python prefix expansion."""

from datetime import datetime
from pathlib import Path

from pyinfra_testing.util import parse_value


def test_datetime_prefix():
    assert parse_value("datetime:2025-01-02T03:04:05") == datetime(2025, 1, 2, 3, 4, 5)


def test_path_prefix():
    result = parse_value("path:/etc/hosts")
    assert isinstance(result, Path)
    assert result == Path("/etc/hosts")


def test_set_marker_in_list():
    assert parse_value(["set:", "a", "b", "a"]) == {"a", "b"}


def test_plain_scalars_unchanged():
    assert parse_value("plain") == "plain"
    assert parse_value(7) == 7
    assert parse_value(True) is True


def test_recurses_into_dicts_and_lists():
    result = parse_value({"when": "datetime:2025-01-01T00:00:00", "where": ["path:/tmp/x"]})
    assert result["when"] == datetime(2025, 1, 1)
    assert result["where"] == [Path("/tmp/x")]


def test_list_without_marker_is_plain_list():
    assert parse_value(["a", "b"]) == ["a", "b"]
