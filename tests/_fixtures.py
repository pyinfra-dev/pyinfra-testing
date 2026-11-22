"""Sample fact and operations used by the end-to-end harness tests.

These stand in for a consumer's custom facts/operations: a fact that parses
command output, and operations exercising enum/dataclass argument coercion,
noop reporting, and reading an injected fact.
"""

import enum
import logging
from dataclasses import dataclass

from pyinfra import host, logger
from pyinfra.api import FactBase, StringCommand, operation


class SampleFact(FactBase):
    def requires_command(self, *args, **kwargs):
        return "echo"

    def command(self):
        return "echo sample"

    def process(self, output):
        return {"value": int(output[0])}


class Color(str, enum.Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class Opts:
    loud: bool | None = None
    tags: list[str] | None = None


@operation()
def sample_op(name, color: Color | None = None, opts: Opts | None = None, present: bool = True):
    if not present:
        host.noop(f"{name} is absent")
        return
    cmd = ["echo", name]
    if color is not None:
        cmd.append(color.value)
    if opts is not None and opts.loud:
        cmd.append("--loud")
    if opts is not None and opts.tags:
        cmd.append(",".join(opts.tags))
    yield StringCommand(*cmd)


@operation()
def fact_op(name):
    fact = host.get_fact(SampleFact, _sudo=True)
    yield StringCommand("echo", name, str(fact["value"]))


@operation()
def logging_op(value):
    logging.getLogger("pyinfra.testing").debug("value=%s", value)
    yield StringCommand("echo", value)


class LoggingFact(SampleFact):
    def process(self, output):
        logger.warning("Processing value %s", output[0])
        return super().process(output)


@operation()
def stateful_op():
    yield StringCommand("echo", host.state.cwd, str(host.state.config.DIFF))
    host.state.cwd = "/changed"
    host.state.config.DIFF = True


class StatefulFact(SampleFact):
    def process(self, output):
        result = {"cwd": host.state.cwd, "diff": host.state.config.DIFF}
        host.state.cwd = "/changed"
        host.state.config.DIFF = True
        return result
