"""Tests for FakeFact (key resolution + attribute access) and FakeHost.get_fact
(global-argument stripping)."""

import enum

import pytest
from pyinfra.api.util import get_kwargs_str

from pyinfra_testing.util import FakeFact, FakeHost, FakeState, create_host


class Proto(str, enum.Enum):
    TCP = "tcp"


# --- FakeFact key resolution ------------------------------------------------ #
def test_exact_string_key():
    fact = FakeFact({"100": {"name": "postgres"}})
    assert fact.get("100") == {"name": "postgres"}


def test_int_lookup_matches_string_key():
    # JSON stores keys as strings; an op doing fact.get(100) must still match.
    fact = FakeFact({"100": {"name": "postgres"}})
    assert fact.get(100) == {"name": "postgres"}
    assert 100 in fact


def test_tuple_lookup_matches_canonical_key():
    fact = FakeFact({'["/", "user", "alice@pve"]': {"role": "PVEAdmin"}})
    assert fact.get(("/", "user", "alice@pve")) == {"role": "PVEAdmin"}
    assert ("/", "user", "alice@pve") in fact


def test_tuple_lookup_with_enum_member_matches():
    # The acl op looks up (path, <coerced enum>, subject).
    fact = FakeFact({'["/", "tcp", "u"]': {"role": "x"}})
    assert fact.get(("/", Proto.TCP, "u")) == {"role": "x"}


def test_missing_key_returns_default():
    fact = FakeFact({"a": 1})
    assert fact.get("missing") is None
    assert fact.get("missing", "fallback") == "fallback"


def test_value_is_attribute_accessible():
    # Operations read fact values as objects (info.field), not just by key.
    fact = FakeFact({"alice": {"role_id": "PVEAdmin", "propagate": True}})
    info = fact.get("alice")
    assert info.role_id == "PVEAdmin"
    assert info.propagate is True
    # Still behaves as a dict.
    assert info["role_id"] == "PVEAdmin"


def test_nested_values_are_attribute_accessible():
    fact = FakeFact({"x": {"inner": {"deep": 1}}})
    assert fact.get("x").inner.deep == 1


def test_attribute_access_missing_field_raises_attribute_error():
    fact = FakeFact({"x": {"a": 1}})
    with pytest.raises(AttributeError):
        fact.get("x").nope


# --- FakeHost.get_fact ------------------------------------------------------ #
class NoArgFact:
    def command(self):
        return "echo hi"


class ArgFact:
    def command(self, name):
        return f"echo {name}"


def _host(facts):
    return create_host(FakeState(), facts=facts)


def test_get_fact_strips_global_sudo_argument():
    key = FakeHost._get_fact_key(NoArgFact)
    host = _host({key: {"value": 7}})
    # _sudo is an execution kwarg, not a fact argument; it must be ignored.
    result = host.get_fact(NoArgFact, _sudo=True)
    assert result == {"value": 7}


def test_get_fact_uses_real_fact_argument_for_lookup():
    key = FakeHost._get_fact_key(ArgFact)
    kwargs_str = get_kwargs_str({"name": "web"})
    host = _host({key: {kwargs_str: {"status": "running"}}})
    # Real positional arg builds the lookup key; _sudo is still stripped.
    result = host.get_fact(ArgFact, "web", _sudo=True)
    assert result == {"status": "running"}


def test_get_fact_missing_fact_raises():
    host = _host({})
    with pytest.raises(KeyError):
        host.get_fact(NoArgFact)


def test_get_fact_missing_arg_key_raises():
    key = FakeHost._get_fact_key(ArgFact)
    host = _host({key: {}})  # fact present, but no entry for name=web
    with pytest.raises(KeyError):
        host.get_fact(ArgFact, "web")
