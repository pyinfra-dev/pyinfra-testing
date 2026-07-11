import copy
import dataclasses
import enum
import json
import os
import re
import types
import typing
from datetime import datetime, timezone
from inspect import Parameter, getcallargs, getfullargspec, signature
from io import StringIO
from os import path
from pathlib import Path
from unittest.mock import patch

from pyinfra.api import Config, Inventory
from pyinfra.api.arguments import all_argument_meta
from pyinfra.api.util import get_kwargs_str


def get_command_string(command):
    value = command.get_raw_value()
    masked_value = command.get_masked_value()
    if value == masked_value:
        return value
    return {"raw": value, "masked": masked_value}


class FakeState:
    active = True
    cwd = "/"
    in_op = True
    in_deploy = True
    pipelining = False
    is_executing = False
    deploy_name = None
    deploy_kwargs = None

    def __init__(self):
        self.inventory = Inventory(([], {}))
        self.config = Config()

    def get_temp_filename(*args):
        return "_tempfile_"


def parse_value(value):
    """
    Convert JSON types to more complex Python types because JSON is lacking.
    """

    if isinstance(value, str):
        if value.startswith("datetime:"):
            return datetime.fromisoformat(value[9:])
        if value.startswith("path:"):
            return Path(value[5:])
        if value.startswith("io:"):
            return StringIO(value[3:])
        return value

    if isinstance(value, list):
        if value and value[0] == "set:":
            return set(parse_value(value) for value in value[1:])
        return [parse_value(value) for value in value]

    if isinstance(value, dict):
        return {key: parse_value(value) for key, value in value.items()}

    return value


def _is_plain_instance(value, annotation):
    """True when ``value`` is already an instance of a concrete, non-generic
    type that is neither an enum nor a dataclass (e.g. ``str``/``int``)."""
    return (
        isinstance(annotation, type)
        and typing.get_origin(annotation) is None
        and not issubclass(annotation, enum.Enum)
        and not dataclasses.is_dataclass(annotation)
        and not isinstance(value, StringIO)
        and isinstance(value, annotation)
    )


def _coerce_value(value, annotation):
    """Coerce a JSON-decoded ``value`` to ``annotation`` when the annotation is
    an enum, a dataclass, or a dict/list container of those. Anything else (and
    any unresolved annotation) is returned unchanged."""
    if value is None or annotation is None:
        return value

    origin = typing.get_origin(annotation)

    # Optional[...] / "X | Y".
    if origin in (typing.Union, types.UnionType):
        members = [m for m in typing.get_args(annotation) if m is not type(None)]
        # If the value already satisfies a "plain" member (e.g. `str` in
        # `Protocol | str`), leave it untouched — the operation explicitly
        # accepts the raw value, so don't force it into the enum/dataclass.
        if any(_is_plain_instance(value, member) for member in members):
            return value
        # Otherwise coerce to the first member whose shape fits the value.
        for member in members:
            member_origin = typing.get_origin(member) or member
            if member_origin is dict and not isinstance(value, dict):
                continue
            if member_origin is list and not isinstance(value, list):
                continue
            return _coerce_value(value, member)
        return value

    # Enum: construct by value (StrEnum / IntEnum / ...).
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        try:
            return annotation(value)
        except ValueError:
            return value

    # Dataclass: build it, recursing into field annotations.
    if dataclasses.is_dataclass(annotation) and isinstance(value, dict):
        try:
            field_hints = typing.get_type_hints(annotation)
        except Exception:
            field_hints = {}
        return annotation(
            **{key: _coerce_value(val, field_hints.get(key)) for key, val in value.items()}
        )

    # dict[K, V] / list[V]: coerce the contents.
    if origin is dict and isinstance(value, dict):
        args = typing.get_args(annotation)
        value_type = args[1] if len(args) == 2 else None
        return {key: _coerce_value(val, value_type) for key, val in value.items()}
    if origin is list and isinstance(value, list):
        args = typing.get_args(annotation)
        value_type = args[0] if args else None
        return [_coerce_value(val, value_type) for val in value]

    return value


def coerce_operation_arguments(op, args, kwargs):
    """Coerce an operation's JSON args/kwargs to its annotated parameter types.

    Uses the operation's own type hints as the source of truth, so test files
    can supply enum members as their plain value (``"amd64"``) and dataclasses
    as plain objects (``{"nesting": true}``) without naming the type. Plain
    values, unannotated parameters, and operations whose hints can't be
    resolved all pass through unchanged.
    """
    fn = getattr(op, "_inner", op)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        return args, kwargs

    positional_params = [
        name
        for name, param in signature(fn).parameters.items()
        if param.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
    ]
    coerced_args = [
        _coerce_value(value, hints.get(positional_params[i]))
        if i < len(positional_params)
        else value
        for i, value in enumerate(args)
    ]
    coerced_kwargs = {key: _coerce_value(value, hints.get(key)) for key, value in kwargs.items()}
    return coerced_args, coerced_kwargs


def canonical_mapping_key(key):
    """Stable string form of a non-string mapping key.

    JSON object keys are always strings, so a fact that returns a dict keyed by
    a tuple (or an int, or an enum) is stored — and must be looked up — under a
    canonical string. A tuple becomes its JSON-array form (``("/", "user")`` ->
    ``'["/", "user"]'``); enum members use their value; everything else uses
    ``str``. The same function is used when normalising a fact result for
    comparison and when resolving a fact lookup, so the two always agree.
    """
    if isinstance(key, str):
        return key
    if isinstance(key, enum.Enum):
        return canonical_mapping_key(key.value)
    if isinstance(key, tuple):
        return json.dumps(
            list(key),
            default=lambda obj: obj.value if isinstance(obj, enum.Enum) else str(obj),
        )
    if key is None:
        return "null"
    if isinstance(key, bool):
        return "true" if key else "false"
    return str(key)


class _AttrDict(dict):
    """A dict whose items are also reachable as attributes.

    Fact values are supplied as plain JSON in test files, but an operation may
    read a fact value as an object (``info.some_field``) rather than by key.
    Returning fact values as ``_AttrDict`` lets both styles work. Real dict
    methods (``get``, ``items``, ...) are untouched; only otherwise-missing
    attributes fall through to item access.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _wrap_fact_value(value):
    """Recursively make dict fact values attribute-accessible."""
    if isinstance(value, dict):
        return _AttrDict((key, _wrap_fact_value(val)) for key, val in value.items())
    if isinstance(value, list):
        return [_wrap_fact_value(val) for val in value]
    return value


class FakeFact:
    def __init__(self, data):
        self.data = parse_value(data)

    def __iter__(self):
        return iter(self.data)

    def __getattr__(self, key):
        return getattr(self.data, key)

    def _resolve_key(self, key):
        # JSON object keys are always strings, so a fact returning e.g. an int-
        # or tuple-keyed dict is stored under string keys. Let non-string
        # lookups (host.get_fact(...).get(100) / .get(("/", "user", "u"))) match
        # the canonical string form used when the fact data was written.
        if not isinstance(self.data, dict) or key in self.data:
            return key
        canonical = canonical_mapping_key(key)
        if canonical in self.data:
            return canonical
        return key

    def __getitem__(self, key):
        return _wrap_fact_value(self.data[self._resolve_key(key)])

    def __setitem__(self, key, value):
        self.data[key] = value

    def __contains__(self, key):
        return self._resolve_key(key) in self.data

    def __call__(self, *args, **kwargs):
        item = self.data

        for arg in args:
            if arg is None:
                continue

            # Support for non-JSON-able fact arguments by  turning them into JSON!
            if isinstance(arg, list):
                arg = json.dumps(arg)

            item = item.get(arg)

        return item

    def __str__(self):
        return str(self.data)

    def __unicode__(self):
        return self.data

    def __eq__(self, other_thing):
        return self.data == other_thing

    def __ne__(self, other_thing):
        return self.data != other_thing

    def get(self, key, default=None):
        if key in self:
            return self[key]

        return default


class FakeFacts:
    def __init__(self, facts):
        self.facts = {key: FakeFact(value) for key, value in facts.items()}

    def __getattr__(self, key):
        return self.facts.get(key)

    def __setitem__(self, key, value):
        self.facts[key] = value

    def _create(self, key, data=None, args=None):
        self.facts[key][args[0]] = data

    def _delete(self, key, args=None):
        self.facts[key].pop(args[0], None)


class FakeHost:
    noop_description = None

    # Current context inside an @operation function
    in_op = True
    in_callback_op = False
    current_op_hash = None
    current_op_global_arguments = None

    # Current context inside a @deploy function
    in_deploy = True
    current_deploy_name = None
    current_deploy_kwargs = None
    current_deploy_data = None

    def __init__(self, state, name, facts, data):
        self.state = state
        self.name = name
        self.fact = FakeFacts(facts)
        self.data = data
        self.connector_data = {}

    @property
    def print_prefix(self):
        return ""

    def noop(self, description):
        self.noop_description = description

    def get_temp_filename(*args, **kwargs):
        return "_tempfile_"

    def get_file(
        self,
        remote_filename,
        filename_or_io,
        remote_temp_filename=None,
        print_output=False,
        *arguments,
    ):
        return True

    def get_temp_dir_config(*args, **kwargs):
        return "_tempdir_"

    @staticmethod
    def _get_fact_key(fact_cls):
        return "{}.{}".format(fact_cls.__module__.split(".")[-1], fact_cls.__name__)

    @staticmethod
    def _check_fact_args(fact_cls, kwargs):
        # Check that the arguments we're going to use to fake a fact are all actual arguments in
        # the fact class, otherwise the test will hide a bug in the underlying operation.
        real_args = getfullargspec(fact_cls.command).args

        for key in kwargs.keys():
            assert key in real_args, (
                f"Argument {key} is not a real argument in the `{fact_cls}.command` method"
            )

    def get_fact(self, fact_cls, *args, **kwargs):
        fact_key = self._get_fact_key(fact_cls)
        fact = getattr(self.fact, fact_key, None)
        if fact is None:
            raise KeyError(f"Missing test fact: {fact_key}")

        # This does the same thing that pyinfra.api.facts._handle_fact_kwargs does:
        # drop the global/executor arguments (_sudo, _su_user, _env, ...) that an
        # operation may pass to host.get_fact, keeping only real fact arguments.
        # These are filtered by the canonical global-argument names rather than by
        # a leading underscore, so genuine fact args like `_id` survive.
        kwargs = {key: value for key, value in kwargs.items() if key not in all_argument_meta}

        if args or kwargs:
            # Merges args & kwargs into a single kwargs dictionary
            kwargs = getcallargs(fact_cls().command, *args, **kwargs)

        if kwargs:
            self._check_fact_args(fact_cls, kwargs)
            kwargs_str = get_kwargs_str(kwargs)
            if kwargs_str not in fact:
                raise KeyError(f"Missing test fact key: {fact_key} -> {kwargs_str}")
            return fact.get(kwargs_str)
        return fact


class FakeFile:
    _read = False
    _data = None

    def __init__(self, name, data=None):
        self._name = name
        self._data = data

    def read(self, *args, **kwargs):
        if self._read is False:
            self._read = True

            if self._data:
                return self._data
            return "_test_data_"

        return ""

    def readlines(self, *args, **kwargs):
        if self._read is False:
            self._read = True

            if self._data:
                return self._data.split()
            return ["_test_data_"]

        return []

    def seek(self, *args, **kwargs):
        pass

    def close(self, *args, **kwargs):
        pass

    def __enter__(self, *args, **kwargs):
        return self

    def __exit__(self, *args, **kwargs):
        pass


class patch_files:
    def __init__(self, local_files):
        directories, files, files_data, symlinks = self._parse_local_files(local_files)

        self._files = files
        self._files_data = files_data
        self._directories = directories
        self._symlinks = symlinks  # dict mapping path -> link_target

    @staticmethod
    def _parse_local_files(local_files, prefix=FakeState.cwd):
        files = []
        files_data = {}
        directories = {}
        symlinks = {}

        prefix = path.normpath(prefix)

        for filename, file_data in local_files.get("files", {}).items():
            filepath = path.join(prefix, filename)
            files.append(filepath)
            files_data[filepath] = file_data

        # Parse symlinks - these are stored as {"name": "target"}
        for linkname, link_target in local_files.get("links", {}).items():
            linkpath = path.join(prefix, linkname)
            symlinks[linkpath] = link_target

        for dirname, dir_files in local_files.get("dirs", {}).items():
            sub_dirname = path.join(prefix, dirname)
            sub_directories, sub_files, sub_files_data, sub_symlinks = (
                patch_files._parse_local_files(
                    dir_files,
                    sub_dirname,
                )
            )

            files.extend(sub_files)
            files_data.update(sub_files_data)
            symlinks.update(sub_symlinks)

            directories[sub_dirname] = {
                "files": list(dir_files.get("files", {}).keys()),
                "dirs": list(dir_files.get("dirs", {}).keys()),
                "links": list(dir_files.get("links", {}).keys()),
            }
            directories.update(sub_directories)

        return directories, files, files_data, symlinks

    def __enter__(self):
        self.patches = [
            patch("pyinfra.operations.files.os.path.exists", self.exists),
            patch("pyinfra.operations.files.os.path.isfile", self.isfile),
            patch("pyinfra.operations.files.os.path.isdir", self.isdir),
            patch("pyinfra.operations.files.os.path.islink", self.islink),
            patch("pyinfra.operations.files.os.readlink", self.readlink),
            patch("pyinfra.operations.files.os.walk", self.walk),
            patch("pyinfra.operations.files.os.stat", self.stat),
            patch("pyinfra.operations.files.os.makedirs", lambda path: True),
            patch("pyinfra.api.util.stat", self.stat),
            # Builtin patches
            patch("pyinfra.operations.files.open", self.get_file, create=True),
            patch("pyinfra.operations.server.open", self.get_file, create=True),
            patch("pyinfra.api.util.open", self.get_file, create=True),
        ]

        for patched in self.patches:
            patched.start()

    def __exit__(self, type_, value, traceback):
        for patched in self.patches:
            patched.stop()

    def get_file(self, filename, *args):
        if self.isfile(filename):
            normalized_path = path.normpath(filename)
            return FakeFile(normalized_path, self._files_data.get(normalized_path))

        raise OSError(f"Missing FakeFile: {filename}")

    def exists(self, filename, *args):
        return self.isfile(filename) or self.isdir(filename) or self.islink(filename)

    def isfile(self, filename, *args):
        normalized_path = path.normpath(filename)
        return normalized_path in self._files

    def isdir(self, dirname, *args):
        normalized_path = path.normpath(dirname)
        return normalized_path in self._directories

    def islink(self, pathname, *args):
        normalized_path = path.normpath(pathname)
        return normalized_path in self._symlinks

    def readlink(self, pathname, *args):
        normalized_path = path.normpath(pathname)
        if normalized_path in self._symlinks:
            return self._symlinks[normalized_path]
        raise OSError(f"No such file or directory: {pathname}")

    def stat(self, pathname):
        try:
            fileinfo = copy.deepcopy(self._files_data[pathname])
            if not fileinfo:
                fileinfo = dict()
        except KeyError:
            fileinfo = dict()

        if self.isfile(pathname):
            default_mode = 33188  # 644 file
        elif self.isdir(pathname):
            default_mode = 16877  # 755 directory
        else:
            raise OSError(f"No such file or directory: {pathname}")

        default_timeval = datetime.fromisoformat("2008-08-09T13:21:44").timestamp()
        defaults = dict(
            mode=default_mode, ino=64321, dev=64556, nlink=1, uid=1001, gid=1001, size=10240
        )

        if "mode" in fileinfo.keys():
            if isinstance(fileinfo["mode"], str):
                perms = int(fileinfo["mode"], 8)
            else:
                # this assumes the mode was provided as an integer whose digits are really octal
                perms = int(str(fileinfo["mode"]), 8)

            if self.isfile(pathname):
                fileinfo["mode"] = 0o100000 + perms
            else:
                fileinfo["mode"] = 0o40000 + perms
        else:
            fileinfo["mode"] = defaults["mode"]

        for field in ["dev", "nlink", "uid", "gid", "size"]:
            if field in fileinfo.keys():
                if isinstance(fileinfo[field], str):
                    fileinfo[field] = int(fileinfo[field])
            else:
                fileinfo[field] = defaults[field]

        # support both "ino" and "inode" as keys for st_ino
        if "ino" in fileinfo.keys():
            if isinstance(fileinfo["ino"], str):
                fileinfo["ino"] = int(fileinfo["ino"])
        elif "inode" in fileinfo.keys():
            if isinstance(fileinfo["inode"], str):
                fileinfo["ino"] = int(fileinfo["inode"])
            else:
                fileinfo["ino"] = fileinfo["inode"]
        else:
            fileinfo["ino"] = defaults["ino"]

        for timefield in ["atime", "mtime", "ctime"]:
            if timefield in fileinfo.keys():
                if not isinstance(fileinfo[timefield], (int, float, str)):
                    raise TypeError("Parameter {0} must have type float, int or str", timefield)

                if isinstance(fileinfo[timefield], str):
                    if fileinfo[timefield].startswith("datetime:"):
                        timestr = fileinfo[timefield].removeprefix("datetime:")
                        dt = datetime.fromisoformat(timestr.strip())
                        if not dt.tzinfo:
                            dt = dt.replace(tzinfo=timezone.utc)
                        fileinfo[timefield] = dt.timestamp()
                    elif re.match("^-?[0-9]+$", fileinfo[timefield].strip()):
                        fileinfo[timefield] = int(fileinfo[timefield].strip())
                    elif re.match("^-?[0-9]+(\\.[0-9]*)?$", fileinfo[timefield].strip()):
                        fileinfo[timefield] = float(fileinfo[timefield].strip())
                    else:
                        raise ValueError(
                            "Invalid argument: {0} for {1}", fileinfo[timefield], timefield
                        )
            else:
                fileinfo[timefield] = default_timeval

        return os.stat_result(
            tuple(
                fileinfo[field]
                for field in [
                    "mode",
                    "ino",
                    "dev",
                    "nlink",
                    "uid",
                    "gid",
                    "size",
                    "atime",
                    "mtime",
                    "ctime",
                ]
            )
        )

    def walk(self, dirname, topdown=True, onerror=None, followlinks=False):
        if not self.isdir(dirname):
            return

        normalized_path = path.normpath(dirname)
        dir_definition = self._directories[normalized_path]
        child_dirs = list(dir_definition.get("dirs", []))
        child_files = list(dir_definition.get("files", []))
        child_links = dir_definition.get("links", [])

        # os.walk reports symlinks in filenames (for file symlinks) or dirnames (for dir symlinks)
        # We add all links to filenames since os.walk includes symlinks to files in filenames
        # and symlinks to directories in dirnames (but won't traverse them if followlinks=False)
        for link_name in child_links:
            # For simplicity, we add all symlinks to filenames since that's what our sync code
            # handles. The sync code then checks os.path.islink() to identify them.
            child_files.append(link_name)

        yield dirname, child_dirs, child_files

        for child in child_dirs:
            full_child = path.join(dirname, child)
            # Don't traverse symlinked directories when followlinks=False
            if not followlinks and self.islink(full_child):
                continue
            yield from self.walk(full_child, topdown, onerror, followlinks)


def create_host(state, name=None, facts=None, data=None):
    """
    Creates a FakeHost object with attached fact data.
    """

    real_facts = {}
    facts = facts or {}
    data = data or {}

    for name, fact_data in facts.items():
        real_facts[name] = fact_data

    return FakeHost(state, name, facts=real_facts, data=data)
