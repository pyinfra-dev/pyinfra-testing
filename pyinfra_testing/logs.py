"""Optional log assertions for generated fact and operation tests."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO

from pyinfra import logger


@contextmanager
def capture_logs(enabled: bool = True) -> Iterator[StringIO]:
    """Capture formatted pyinfra logs and restore logging settings afterward."""
    output = StringIO()
    if not enabled:
        yield output
        return

    handler = logging.StreamHandler(output)
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield output
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()


def assert_logs(messages: str, expected: dict[str, list[str]] | None) -> None:
    """Check captured messages against a case's contains/excludes expectations."""
    if expected is None:
        return

    if not isinstance(expected, dict) or expected.keys() - {"contains", "excludes"}:
        raise ValueError("expected_logs must be a mapping with contains/excludes lists")
    for values in expected.values():
        if not isinstance(values, list) or not all(
            isinstance(item, str) for item in values
        ):
            raise ValueError("expected_logs contains/excludes must be lists of strings")

    for message in expected.get("excludes", []):
        if message in messages:
            raise AssertionError(
                f"Unexpected substring in pyinfra logs: {message!r}\n"
                f"Captured logs:\n{messages}"
            )
    for message in expected.get("contains", []):
        if message not in messages:
            raise AssertionError(
                f"Expected substring missing from pyinfra logs: {message!r}\n"
                f"Captured logs:\n{messages}"
            )
