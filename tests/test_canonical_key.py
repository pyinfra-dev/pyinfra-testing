"""Tests for canonical_mapping_key — the single key form shared by fact-result
serialisation and operation fact lookups, so the two always agree."""

import enum
import json

from pyinfra_testing.util import canonical_mapping_key


class Proto(str, enum.Enum):
    TCP = "tcp"
    UDP = "udp"


def test_string_key_is_unchanged():
    assert canonical_mapping_key("root@pam") == "root@pam"


def test_int_key_becomes_its_string():
    # JSON object keys for ints round-trip to their string form ("100").
    assert canonical_mapping_key(100) == "100"


def test_bool_and_none_match_json_forms():
    assert canonical_mapping_key(True) == "true"
    assert canonical_mapping_key(False) == "false"
    assert canonical_mapping_key(None) == "null"


def test_enum_key_uses_its_value():
    assert canonical_mapping_key(Proto.TCP) == "tcp"


def test_tuple_key_is_json_array():
    key = canonical_mapping_key(("/", "user", "alice@pve"))
    assert key == '["/", "user", "alice@pve"]'
    # ...and it is valid JSON round-tripping back to the list.
    assert json.loads(key) == ["/", "user", "alice@pve"]


def test_tuple_key_mixes_scalars():
    assert canonical_mapping_key(("/vms", 100)) == '["/vms", 100]'


def test_tuple_with_enum_member_uses_value():
    # An operation looking up a tuple containing a (coerced) enum must produce
    # the same string as a fact result whose key holds the raw value.
    from_lookup = canonical_mapping_key(("/", Proto.TCP, "u"))
    from_result = canonical_mapping_key(("/", "tcp", "u"))
    assert from_lookup == from_result == '["/", "tcp", "u"]'
