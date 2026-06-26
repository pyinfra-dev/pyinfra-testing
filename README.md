# pyinfra testing utils

Generate `unittest` tests for pyinfra facts and operations from data files
(JSON/YAML) instead of hand-writing assertions.

You point a generator at a fact or operation and a folder of test-case files.
The harness creates a `TestCase` subclass with **one test method per file** in
that folder, so adding a case is just dropping in another `.json`/`.yaml`.

## Install

From PyPI as a development dependency, using `uv`:

```bash
uv add --dev pyinfra-testing
```

This adds it to your `dev` dependency group:

```toml
# pyproject.toml
[dependency-groups]
dev = ["pyinfra-testing>=0.2.0"]
```

Then `uv sync` to install. The generated classes are plain
`unittest.TestCase`s, so `pytest` (or `python -m unittest`) discovers them with
no extra config.

## Layout

The conventional layout uses one directory per fact/operation, named after its
dotted import path, with the case files inside:

```
tests/
  test_facts.py                 # discovers tests/facts/*/
  facts/
    proxmox.pve.PVEContainers/  # dir name == "<module>.<FactClass>"
      two_running.json
      empty.json
  test_operations.py            # discovers tests/operations/*/
  operations/
    proxmox.pve.container/      # dir name == "<module>.<operation>"
      create_minimal.json
      destroy.json
```

`test_facts.py` just iterates those directories and hands each one to the
generator (the directory name *is* the dotted path argument):

```python
# tests/test_facts.py
from pathlib import Path
from pyinfra_testing.facts import make_fact_tests

BASE_IMPORT_PATH = "facts"          # your facts package root
TESTS_BASE = Path(__file__).parent / "facts"

for fact_path in sorted(d.name for d in TESTS_BASE.iterdir() if d.is_dir()):
    locals()[fact_path] = make_fact_tests(BASE_IMPORT_PATH, fact_path, TESTS_BASE / fact_path)
```

```python
# tests/test_operations.py
from pathlib import Path
from pyinfra_testing.operations import make_operation_tests

BASE_IMPORT_PATH = "operations"
TESTS_BASE = Path(__file__).parent / "operations"

for op_path in sorted(d.name for d in TESTS_BASE.iterdir() if d.is_dir()):
    locals()[op_path] = make_operation_tests(BASE_IMPORT_PATH, op_path, TESTS_BASE / op_path)
```

`make_*_tests(base_import_path, dotted_path, folder)` imports
`base_import_path + "." + dotted_path` and resolves the trailing attribute as
the fact class / operation. Both **flat** (`pyinfra.facts` + `server.LinuxName`)
and **nested** (`facts` + `proxmox.pve.PVEContainers`) namespaces work — the
final `.`-segment is the attribute, everything before it is the module.

A test directory must contain **only** case files: every `.json`/`.yaml`/`.yml`
in it becomes one test.

## Testing facts

A fact case file describes the command output and the expected parsed result:

```yaml
# facts/proxmox.pve.PVEContainers/two_running.yaml
# `output`: the raw lines the command would print (a str is split on newlines).
output: |
  VMID       Status     Lock         Name
  100        running                 postgres
  102        running    backup       web-server
# `fact`: the expected result of fact.process(output), JSON-normalised.
fact:
  "100": { vmid: 100, status: running, lock: null, name: postgres }
  "102": { vmid: 102, status: running, lock: backup, name: web-server }
# Optional — asserted when present, otherwise a warning is emitted:
# command: pct list
# requires_command: pveum
# arg: [100]          # args passed when instantiating the fact class
# facts: { ... }      # other facts the fact's process() reads via the host
```

`process()` runs under a frozen clock (`2025-01-01`) so facts that use the
current date are deterministic.

### Supported fact return types

Before comparison the result of `process()` is run through
`json.dumps(..., default=...)` and back. A fact may return anything that
survives that round-trip:

| Returned value | Serialised as | Notes |
|---|---|---|
| `dict`, `list`, `str`, `int`, `float`, `bool`, `None` | themselves | the JSON-native types |
| **dataclass instance** | object via `dataclasses.asdict` | nested dataclasses, and dataclasses inside `dict`/`list`, are handled recursively |
| `enum.StrEnum` / `enum.IntEnum` (or any `Enum` subclassing `str`/`int`) | its underlying value | a **plain `Enum`** is *not* serialisable — make it a `StrEnum`/`IntEnum` |
| `datetime` | ISO-8601 string | |
| `pathlib.Path` | `str` | |
| `set` | sorted `list` | |
| `bytes` | decoded `str` | |
| object exposing `to_json()` | whatever `to_json()` returns | pyinfra's own extension hook; also used at runtime by the CLI |

Anything else raises `TypeError: Cannot serialize: ...`. If you need a custom
shape (omit `None` fields, rename keys, serialise a plain `Enum`), give the
returned type a `to_json()` method — it takes precedence and is the same hook
pyinfra uses to serialise facts at runtime.

Two consequences worth noting when writing the expected `fact`:

- **Dict keys become strings.** JSON object keys are always strings, so a fact
  returning `dict[int, ...]` (e.g. keyed by VMID) must use string keys in the
  expected `fact` — `"100":` not `100:`. A fact keyed by a **tuple** is encoded
  with its JSON-array form as the key — `dict[tuple, ...]` keyed by
  `("/", "user", "alice@pve")` becomes the key `"[\"/\", \"user\", \"alice@pve\"]"`.
- **`asdict` emits every field**, including those left at their default/`None`.
  The expected `fact` must list them too (or give the dataclass a `to_json()`
  that drops them).

## Testing operations

An operation case file describes the inputs and the exact commands the
operation should yield:

```yaml
# operations/proxmox.pve.container/create_minimal.yaml
args: [100, "ubuntu-22.04-standard_22.04-1_amd64.tar.zst"]
kwargs: { present: true }
# `facts`: data returned by host.get_fact(...) inside the operation, keyed by
# "<module>.<FactClass>". Global/executor kwargs the operation passes to
# get_fact (_sudo, _su_user, ...) are ignored when matching; real fact args
# (positional, or kwargs like _id) are used to build the lookup key.
facts:
  pve.PVEContainers: {}        # no existing containers
# `commands`: the exact list the operation should yield.
commands:
  - pct create 100 ubuntu-22.04-standard_22.04-1_amd64.tar.zst
# Optional:
# noop_description: "Container '175' already exists. Use force=True to recreate."
# exception: { name: ValueError, message: "..." }   # or names: [A, B]
# require_platform: [Linux]                          # skip on other platforms
# local_files: { files: {...}, dirs: {...}, links: {...} }
```

Operations are invoked via `op._inner(*args, **kwargs)`. If a case yields no
commands, set `noop_description` to assert the operation called `host.noop(...)`.

Injected `facts` values are returned to the operation as attribute-accessible
dicts, so an operation that reads a fact value as an object
(`info.some_field`) works against plain JSON data. Lookup keys are matched
JSON-style — an operation doing `fact.get(100)` matches the `"100"` key, and
`fact.get(("/", "user", "alice@pve"))` matches the tuple's JSON-array key
`"[\"/\", \"user\", \"alice@pve\"]"` (the same canonical form a `dict[tuple, ...]`
fact result is encoded with). So facts keyed by ints, enums, or tuples can all
be supplied; key the JSON `facts` data with that canonical string.

Yielded commands may be plain strings, `StringCommand`, `FunctionCommand`,
`FileUploadCommand`/`FileDownloadCommand` (compared as `["upload", data, dest]`
/ `["download", src, dest]`), or a connector-argument dict.

### Enum and dataclass arguments

`args`/`kwargs` are coerced to the operation's annotated parameter types using
the operation's own signature, so you write plain JSON and the types are
reconstructed for you:

- a parameter annotated as an `Enum` (e.g. `arch: PVEContainerArch | None`)
  receives the member built from the JSON value — `"arch": "amd64"` becomes
  `PVEContainerArch.AMD64`;
- a parameter annotated as a dataclass receives an instance built from a JSON
  object — `"features": {"nesting": true}` becomes `Features(nesting=True)`;
- `dict[..., T]` / `list[T]` containers of those are coerced element-wise —
  `"networks": {"0": {"name": "eth0"}}` becomes `{"0": NetworkInterface(...)}`.

Coercion only applies where the annotation is an enum, a dataclass, or a
container of them. Plain parameters, unannotated parameters, and union members
the value already satisfies (e.g. the `str` in `Protocol | str`) are left
untouched, and an operation whose hints can't be resolved is skipped entirely.

### Typed values in args/kwargs

The coercion above is driven by parameter types. For values whose target type
is *not* visible in the signature, JSON/YAML strings are also expanded by
prefix:

- `"datetime:2025-01-01T00:00:00"` → `datetime`
- `"path:/etc/hosts"` → `pathlib.Path`
- `["set:", "a", "b"]` → `set`

## Running

```bash
uv run pytest                      # all generated (+ any hand-written) tests
uv run pytest tests/test_facts.py  # just the fact suites
```
