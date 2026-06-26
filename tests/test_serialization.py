"""Tests for fact-result serialisation: _jsonable_key / _to_jsonable (tuple-key
and dataclass normalisation) and _json_default (dataclass / leaf encoding)."""

import json
from dataclasses import dataclass
from datetime import datetime

from pyinfra_testing.facts import _json_default, _jsonable_key, _to_jsonable


@dataclass
class Info:
    a: int
    b: str


def test_jsonable_key_keeps_json_scalars():
    assert _jsonable_key("s") == "s"
    assert _jsonable_key(100) == 100
    assert _jsonable_key(True) is True
    assert _jsonable_key(None) is None


def test_jsonable_key_stringifies_tuple():
    assert _jsonable_key(("/", "user")) == '["/", "user"]'


def test_to_jsonable_expands_dataclass():
    assert _to_jsonable(Info(1, "x")) == {"a": 1, "b": "x"}


def test_to_jsonable_rewrites_tuple_keys():
    assert _to_jsonable({("a", "b"): 1}) == {'["a", "b"]': 1}


def test_to_jsonable_recurses_dataclass_values_under_tuple_keys():
    assert _to_jsonable({("/", "u"): Info(1, "x")}) == {'["/", "u"]': {"a": 1, "b": "x"}}


def test_to_jsonable_leaves_unknown_leaves_for_default():
    # Sets/datetimes aren't handled here; they survive for _json_default.
    data = _to_jsonable({"s": {1, 2}})
    assert data == {"s": {1, 2}}


def test_json_default_serialises_dataclass():
    assert _json_default(Info(1, "x")) == {"a": 1, "b": "x"}


def test_json_default_serialises_leaf_types():
    # Defers to pyinfra's json_encode for sets / datetimes.
    assert _json_default({3, 1, 2}) == [1, 2, 3]
    assert _json_default(datetime(2025, 1, 1)) == "2025-01-01T00:00:00"


def test_full_pipeline_tuple_keyed_dataclass_dict():
    # This mirrors exactly what make_fact_tests does before comparison.
    data = {("/", "user", "alice@pve"): Info(1, "x")}
    out = json.loads(json.dumps(_to_jsonable(data), default=_json_default))
    assert out == {'["/", "user", "alice@pve"]': {"a": 1, "b": "x"}}
