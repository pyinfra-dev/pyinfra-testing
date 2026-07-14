"""Tests for enum/dataclass argument coercion (_coerce_value and
coerce_operation_arguments)."""

import enum
from dataclasses import dataclass

from pyinfra_testing.util import _coerce_value, coerce_operation_arguments


class Arch(str, enum.Enum):
    AMD64 = "amd64"
    ARM64 = "arm64"


class Proto(str, enum.Enum):
    TCP = "tcp"
    UDP = "udp"


@dataclass
class Net:
    name: str
    tag: int | None = None


@dataclass
class Feat:
    nesting: bool | None = None
    mount: list[str] | None = None


# --- _coerce_value: enums --------------------------------------------------- #
def test_enum_value_is_constructed():
    assert _coerce_value("amd64", Arch) is Arch.AMD64


def test_optional_enum():
    assert _coerce_value("arm64", Arch | None) is Arch.ARM64


def test_invalid_enum_value_is_left_unchanged():
    assert _coerce_value("sparc", Arch) == "sparc"


def test_none_passes_through():
    assert _coerce_value(None, Arch | None) is None


# --- _coerce_value: the `Enum | str` regression case ------------------------ #
def test_union_with_str_keeps_the_string():
    # `Protocol | str` accepts the raw string, so it must NOT be forced to the
    # enum (this was the selinux.port regression).
    result = _coerce_value("tcp", Proto | str)
    assert result == "tcp"
    assert type(result) is str
    assert not isinstance(result, Proto)


# --- _coerce_value: dataclasses --------------------------------------------- #
def test_dataclass_is_built():
    assert _coerce_value({"name": "eth0", "tag": 5}, Net) == Net("eth0", 5)


def test_dataclass_uses_defaults_for_missing_fields():
    assert _coerce_value({"name": "eth0"}, Net) == Net("eth0", None)


def test_dataclass_with_list_field():
    assert _coerce_value({"nesting": True, "mount": ["nfs", "cifs"]}, Feat) == Feat(
        nesting=True, mount=["nfs", "cifs"]
    )


# --- _coerce_value: containers ---------------------------------------------- #
def test_dict_values_are_coerced_first_matching_union_member():
    result = _coerce_value({"0": {"name": "eth0"}}, dict[int, Net] | list[Net] | None)
    assert result == {"0": Net("eth0")}
    assert isinstance(result["0"], Net)


def test_list_items_are_coerced():
    result = _coerce_value([{"name": "eth0"}, {"name": "eth1", "tag": 9}], list[Net])
    assert result == [Net("eth0"), Net("eth1", 9)]


def test_plain_values_untouched():
    assert _coerce_value("hello", str) == "hello"
    assert _coerce_value(5, int) == 5


# --- coerce_operation_arguments --------------------------------------------- #
def op_positional(a: Arch, b: int, present: bool = True):
    pass


def op_kwargs(name, color: Arch | None = None, feat: Feat | None = None):
    pass


def op_plain(name: str, count: int):
    pass


def op_unresolved(x: "NopeNotARealType", y=1):  # noqa: F821 - intentional
    pass


def test_positional_enum_arg_is_coerced_by_signature():
    args, kwargs = coerce_operation_arguments(op_positional, ["amd64", 3], {})
    assert args == [Arch.AMD64, 3]
    assert kwargs == {}


def test_keyword_enum_and_dataclass_are_coerced():
    args, kwargs = coerce_operation_arguments(
        op_kwargs, ["n"], {"color": "arm64", "feat": {"nesting": True}}
    )
    assert args == ["n"]
    assert kwargs == {"color": Arch.ARM64, "feat": Feat(nesting=True)}


def test_plain_signature_is_untouched():
    args, kwargs = coerce_operation_arguments(op_plain, ["n"], {"count": 2})
    assert (args, kwargs) == (["n"], {"count": 2})


def test_unresolvable_hints_fall_back_to_inputs():
    # get_type_hints raises for the bogus annotation; coercion must no-op.
    args, kwargs = coerce_operation_arguments(op_unresolved, ["a"], {"y": 2})
    assert (args, kwargs) == (["a"], {"y": 2})


def test_extra_positional_args_beyond_signature_pass_through():
    # More positional values than named params (e.g. *args) are left as-is.
    args, kwargs = coerce_operation_arguments(op_plain, ["n", 2, "extra"], {})
    assert args == ["n", 2, "extra"]


# --- _coerce_value: tuples -------------------------------------------------- #
def test_tuple_is_built_from_list():
    assert _coerce_value([1, 2, 3], tuple[int, ...]) == (1, 2, 3)


def test_tuple_elements_are_coerced():
    assert _coerce_value([{"name": "eth0"}], tuple[Net, ...]) == (Net("eth0"),)


def test_fixed_size_tuple_is_coerced():
    assert _coerce_value(["a", 1], tuple[str, int]) == ("a", 1)
