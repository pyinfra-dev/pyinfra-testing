import dataclasses
import json
import warnings
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any
from unittest import TestCase

from freezegun import freeze_time
from pyinfra.api import StringCommand
from pyinfra.api.facts import ShortFactBase
from pyinfra.context import ctx_host, ctx_state
from pyinfra_cli.util import json_encode

from .testgen import TestGenerator
from .util import FakeState, canonical_mapping_key, create_host, get_command_string

# show full diff on jsons
TestCase.maxDiff = None


def _json_default(obj: Any) -> Any:
    """JSON encoder for fact data.

    Extends pyinfra's own ``json_encode`` with support for plain dataclass
    instances: a fact that returns a dataclass (or a structure nesting them)
    is serialised via ``dataclasses.asdict`` so it can be compared against the
    JSON ``fact`` defined in a test file. Everything else (datetimes, Paths,
    sets, objects exposing ``to_json``, ...) defers to ``json_encode``.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return json_encode(obj)


def _jsonable_key(key: Any) -> Any:
    """Leave JSON-scalar keys as-is (json stringifies them natively); convert
    tuple/enum/other keys to their canonical string form."""
    if key is None or isinstance(key, (str, int, float, bool)):
        return key
    return canonical_mapping_key(key)


def _to_jsonable(data: Any) -> Any:
    """Normalise a fact result so ``json.dumps`` can encode it.

    Expands dataclasses (so nested dicts are reached) and rewrites any
    non-JSON-scalar mapping keys — e.g. the tuple keys of a fact returning
    ``dict[tuple, ...]`` — to a canonical string. Leaf types that ``json_encode``
    already handles (datetimes, sets, ...) are left for ``_json_default``.
    """
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        data = dataclasses.asdict(data)
    if isinstance(data, dict):
        return {_jsonable_key(key): _to_jsonable(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [_to_jsonable(value) for value in data]
    return data


def _make_command(command_attribute: str | Callable[..., str], args: dict[str, Any] | list[Any]) -> str:
    if callable(command_attribute):
        if isinstance(args, dict):
            return command_attribute(**args)

        if not isinstance(args, list):
            args = [args]

        return command_attribute(*args)
    return command_attribute


def make_fact_tests(base_import_path: str, fact_path: str, tests_folder: Path) -> type[TestCase]:
    module_name, fact_name = fact_path.rsplit(".", 1)
    module = import_module(f"{base_import_path}.{module_name}")
    fact = getattr(module, fact_name)()

    class TestTests(
        TestCase,
        metaclass=TestGenerator,
        tests_dir=tests_folder,
        test_prefix=f"test_{fact.name}_",
        test_method="_test",
    ):
        @classmethod
        def setUpClass(cls):
            # Create a global fake state that attach to context state
            cls.state = FakeState()

        def _test(self, test_name, test_data, fact=fact):
            host = create_host(self.state, facts=test_data.get("facts", {}))
            with ctx_state.use(self.state):
                with ctx_host.use(host):
                    self._test_fn(test_name, test_data, fact)

        def _test_fn(self, test_name, test_data, fact):
            short_fact = None

            if isinstance(fact, ShortFactBase):
                short_fact = fact
                fact = fact.fact()

            test_args = test_data.get("arg", [])
            command = _make_command(fact.command, test_args)

            if "command" in test_data:
                assert get_command_string(StringCommand(command)) == test_data["command"]
            else:
                warnings.warn(
                    f'No command set for test: {test_name} (got "{command}")',
                )

            requires_command = _make_command(fact.requires_command, test_args)

            if requires_command:
                if "requires_command" in test_data:
                    assert requires_command == test_data["requires_command"]
                else:
                    warnings.warn(
                        f'No requires command set for test: {test_name} (got "{requires_command}")',
                    )

            command_output = test_data["output"]
            if isinstance(command_output, str):
                command_output = command_output.splitlines()

            # Freeze the date so any facts that use the "current" year will used 2025
            data = freeze_time("2025-01-01")(fact.process)(command_output)
            if short_fact:
                data = short_fact.process_data(data)

            # Encode/decode data to ensure datetimes/dataclasses/tuple-keys/etc
            # become JSON
            data = json.loads(json.dumps(_to_jsonable(data), default=_json_default))
            try:
                assert data == test_data["fact"]
            except AssertionError as e:
                print()
                print("--> GOT:\n", json.dumps(data, indent=4, default=json_encode))
                print(
                    "--> WANT:",
                    json.dumps(
                        test_data["fact"],
                        indent=4,
                        default=json_encode,
                    ),
                )
                raise e

    TestTests.__name__ = f"Fact{fact.name}"
    return TestTests
