"""tooled's verbs as parsed commands, and its command line read by parsed's argv front end.

Each verb is a command class (``fetch {contract} {slots}``), its flags its fields, read by parsed
(:func:`parsed.argv.read`, then the app's walk). A base gives every verb that reads the policy its own flags
(``--policy``, ``--pretty``, ``--select``, ``--max-bytes``), and ``--out`` and ``--session`` where they apply; they
follow the verb, as they always have, so the program's one root option is ``--version``. ``session``'s verbs are a
tree of their own, mounted under ``session``.

**tooled's own kinds** read what parsed would read otherwise, so a refusal says what tooled always has: a timeout in
seconds (:class:`Seconds`, a bare ``3`` among them), a duration (:class:`Duration`: ``30``, ``30s``, ``5m``, ``1h``),
an idle timeout (``0`` for never), a count, a byte cap, a running process, ``--select``'s paths, ``--until``'s
conditions, ``--verbs``' picks and ``--format``'s forms.

**A line's verbs** (``see``, ``do``) take the rest of the line as the app's (``parsed.kinds.Line``), from its first
word: its flags, ``--yes`` and ``--until`` among them, are the line's. parsed's own flags given before it
(``do --yes dock vesta``) are put back on it, and a line given as one argument (``do 'fleet f1 mine'``) is that line.
``session run`` gives its process the command's words as given (:func:`parsed.argv.args_of`).

:func:`read` gives a program's arguments read: the verb's command, parsed's own flags given it, or the help or the
version they ask for.
"""

from __future__ import annotations

import functools
import math
import os
from collections.abc import Mapping  # noqa: TC003 -- at runtime: parsed evaluates a command's annotations
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 -- at runtime: parsed evaluates a command's annotations
from typing import TYPE_CHECKING, ClassVar

import parsed
from parsed.argv import args_of, options_of
from parsed.argv import read as read_args

from .errors import UsageError
from .sessions import WAIT

if TYPE_CHECKING:
    from collections.abc import Sequence

    from seared import Field

#: A fetch's, a query's and a write's bound, in seconds, when none is given.
ASKED = 5.0

#: How long a line's verb waits on its line, in seconds, when none is given: longer for a line that waits itself.
LINE_BOUND = 30.0

#: How long a wait waits, in seconds, when no ``--timeout`` is given.
WAITED = 30.0

#: The description ``tooled --help`` opens with.
DESCRIPTION = 'Typed, policy-guarded access to a zeared mesh, for agents and people.'

#: The end of ``tooled --help``: where output goes, and what each exit code means.
EPILOG = """\
Results go to stdout as JSON. Errors go to stderr as one JSON object, notes
and log lines as JSON lines. A result over the size cap (--max-bytes, else
$TOOLED_MAX_BYTES, else 28000 bytes) is refused with each part's size:
--select keeps parts, --out writes it whole.

exit codes:
  0  done (an empty result included)
  2  usage, the policy file, or the protocol
  3  refused by the policy
  4  a request, or a tool call's arguments, that do not load: the field at
     fault is in "path"
  5  the mesh: the session would not open, a read or a write failed, or a
     write got no reply: "generated" holds its generated values
  6  a result over the size cap
  7  wait: no value met the conditions in time; "last" holds the last one seen

The policy is --policy PATH, else $TOOLED_POLICY, else ./tooled.toml. With
$TOOLED_LOG set to a path, each run appends one JSON line to it: what ran,
by name, how it ended, its bytes and seconds, and no value.
"""

#: parsed's own flags a line's verb puts back on its line, given before it, in the order a line says them.
PASSED_ON = ('json', 'yes', 'dry-run')

#: The flags parsed answers itself: help, the one each verb takes.
_HELP = 'help'


# tooled's own kinds: each reads a word as tooled always has, and says so where it can't.


@dataclass(frozen=True, slots=True)
class Seconds(parsed.Kind):
    """A timeout: a number of seconds above zero (``3``, ``0.5``), as a ``Float``."""

    def form(self, word: str, field: Field) -> object:  # noqa: ARG002 -- every kind reads alike
        """The seconds, once they're a finite number above zero."""
        try:
            value = float(word)
        except ValueError:
            value = 0.0
        if not math.isfinite(value) or value <= 0:
            msg = f'a timeout is a number of seconds above zero, not {word}'
            raise ValueError(msg)
        return value

    def what(self, field: Field) -> str:  # noqa: ARG002 -- every kind describes alike
        """``a number of seconds``."""
        return 'a number of seconds'

    def usage_label(self, field: Field) -> str | None:  # noqa: ARG002 -- every kind labels alike
        """``seconds``."""
        return 'seconds'


_UNITS = {'s': 1.0, 'm': 60.0, 'h': 3600.0}


def duration(word: str) -> float:
    """``30``, ``30s``, ``5m`` or ``1h``, as seconds above zero.

    Raises:
        ValueError: It's none of them.
    """
    unit = word[-1:] if word[-1:] in _UNITS else 's'
    number = word[:-1] if word[-1:] in _UNITS else word
    try:
        value = float(number) * _UNITS[unit]
    except ValueError:
        value = 0.0
    if not math.isfinite(value) or value <= 0:
        msg = f'a duration is seconds above zero, or with s, m or h: 30, 30s, 5m, 1h; not {word}'
        raise ValueError(msg)
    return value


@dataclass(frozen=True, slots=True)
class Duration(parsed.Kind):
    """A duration: seconds, or with ``s``, ``m`` or ``h`` (``30``, ``30s``, ``5m``, ``1h``), as a ``Float``.

    Attributes:
        never: Whether ``0`` (``0s``, ``0m``, ``0h``) reads, as ``0.0``: an idle timeout's never.
    """

    never: bool = False

    def form(self, word: str, field: Field) -> object:  # noqa: ARG002 -- every kind reads alike
        """The seconds the word says; ``0.0`` for a never's zero."""
        if self.never and word.strip() in {'0', '0s', '0m', '0h'}:
            return 0.0
        return duration(word)

    def what(self, field: Field) -> str:  # noqa: ARG002 -- every kind describes alike
        """``a duration: 30, 30s, 5m, 1h``, and ``0`` for never where it reads."""
        return 'a duration: 30, 30s, 5m, 1h' + (', or 0 for never' if self.never else '')

    def usage_label(self, field: Field) -> str | None:  # noqa: ARG002 -- every kind labels alike
        """``duration``."""
        return 'duration'


@dataclass(frozen=True, slots=True)
class Whole(parsed.Kind):
    """A whole number at least ``least``, as an ``Int``: a count (``1``), or a byte cap (``0``, none).

    Attributes:
        least: The least it may be.
        what_it_is: What it is, as a refusal says it: ``a count``.
        label: What a usage writes it as.
    """

    least: int = 1
    what_it_is: str = 'a count'
    label: str = 'n'

    def form(self, word: str, field: Field) -> object:  # noqa: ARG002 -- every kind reads alike
        """The number, once it's whole and at least :attr:`least`."""
        try:
            value = int(word)
        except ValueError:
            value = self.least - 1
        if value < self.least:
            bound = 'above zero' if self.least == 1 else 'zero or more'
            msg = f'{self.what_it_is} is a whole number {bound}, not {word}'
            raise ValueError(msg)
        return value

    def what(self, field: Field) -> str:  # noqa: ARG002 -- every kind describes alike
        """``a whole number above zero``, or ``zero or more``."""
        return 'a whole number ' + ('above zero' if self.least == 1 else 'zero or more')

    def usage_label(self, field: Field) -> str | None:  # noqa: ARG002 -- every kind labels alike
        """Its :attr:`label`."""
        return self.label


@dataclass(frozen=True, slots=True)
class Process(parsed.Kind):
    """A process that's running, by its id, as an ``Int``."""

    def form(self, word: str, field: Field) -> object:  # noqa: ARG002 -- every kind reads alike
        """The id, once a process by it is running, as this user or another."""
        try:
            value = int(word)
        except ValueError:
            value = 0
        running = value > 0
        if running:
            try:
                os.kill(value, 0)
            except ProcessLookupError:
                running = False
            except PermissionError:
                pass  # running, as another user
        if not running:
            msg = f'no process {word} is running'
            raise ValueError(msg)
        return value

    def what(self, field: Field) -> str:  # noqa: ARG002 -- every kind describes alike
        """``a running process's id``."""
        return "a running process's id"

    def usage_label(self, field: Field) -> str | None:  # noqa: ARG002 -- every kind labels alike
        """``pid``."""
        return 'pid'


@dataclass(frozen=True, slots=True)
class Selected(parsed.Kind):
    """``--select``'s paths, comma-separated (``a.b,list[].key``): checked here, kept as written."""

    def form(self, word: str, field: Field) -> object:  # noqa: ARG002 -- every kind reads alike
        """The word, once its paths read (:func:`tooled.output.parse_paths`)."""
        from .output import parse_paths

        try:
            parse_paths(word)
        except UsageError as exc:
            raise ValueError(exc.message) from None
        return word

    def what(self, field: Field) -> str:  # noqa: ARG002 -- every kind describes alike
        """``dotted paths, comma-separated: a.b, list[].key``."""
        return 'dotted paths, comma-separated: a.b, list[].key'

    def usage_label(self, field: Field) -> str | None:  # noqa: ARG002 -- every kind labels alike
        """``paths``."""
        return 'paths'


@dataclass(frozen=True, slots=True)
class Condition(parsed.Kind):
    """A ``--until`` condition, ``path=value``: checked here, kept as written."""

    def form(self, word: str, field: Field) -> object:  # noqa: ARG002 -- every kind reads alike
        """The word, once it reads as a condition (:meth:`tooled.executor.Condition.parse`)."""
        from .executor import Condition as Parsed

        try:
            Parsed.parse(word)
        except UsageError as exc:
            raise ValueError(exc.message) from None
        return word

    def example(self, field: Field) -> object:  # noqa: ARG002 -- every kind gives alike
        """``docked=true``."""
        return 'docked=true'

    def what(self, field: Field) -> str:  # noqa: ARG002 -- every kind describes alike
        """``a dotted path and the value it must equal``."""
        return 'a dotted path and the value it must equal: numbers.fuel=40'

    def usage_label(self, field: Field) -> str | None:  # noqa: ARG002 -- every kind labels alike
        """``path=value``."""
        return 'path=value'


@dataclass(frozen=True, slots=True)
class Picks(parsed.Kind):
    """``--verbs``' picks, comma-separated: a verb for all of its tools, or ``verb:GLOB``. Kept as written."""

    def form(self, word: str, field: Field) -> object:  # noqa: ARG002 -- every kind reads alike
        """The word, once each pick names a verb that has tools."""
        from .tools import CALLS

        unknown = [pick.strip() for pick in word.split(',') if pick.strip().partition(':')[0] not in CALLS]
        if unknown:
            msg = f'each is a verb, or verb:GLOB, of {", ".join(CALLS)}; not {", ".join(map(repr, unknown))}'
            raise ValueError(msg)
        return word

    def what(self, field: Field) -> str:  # noqa: ARG002 -- every kind describes alike
        """``verbs, or verb:GLOB, comma-separated``."""
        return 'verbs, or verb:GLOB, comma-separated: fetch,wait:Fleet*'

    def usage_label(self, field: Field) -> str | None:  # noqa: ARG002 -- every kind labels alike
        """``verbs``."""
        return 'verbs'


@dataclass(frozen=True, slots=True)
class Form(parsed.Kind):
    """The form tool definitions are written in: one of :data:`tooled.tools.FORMATS`."""

    def form(self, word: str, field: Field) -> object:  # noqa: ARG002 -- every kind reads alike
        """The word, once it's one of the forms."""
        from .tools import FORMATS

        if word not in FORMATS:
            msg = f'{word!r} is no form tooled writes: {", ".join(FORMATS)}'
            raise ValueError(msg)
        return word

    def offers(self, field: Field) -> tuple[str, ...]:  # noqa: ARG002 -- every kind offers alike
        """Each form."""
        from .tools import FORMATS

        return FORMATS

    def what(self, field: Field) -> str:  # noqa: ARG002 -- every kind describes alike
        """``openai, anthropic or mcp``."""
        from .tools import FORMATS

        return ', '.join(FORMATS[:-1]) + f' or {FORMATS[-1]}'

    def usage_label(self, field: Field) -> str | None:  # noqa: ARG002 -- every kind labels alike
        """``openai|anthropic|mcp``."""
        from .tools import FORMATS

        return '|'.join(FORMATS)


SECONDS = Seconds()
DURATION = Duration()
LINE = parsed.kinds.Line()


# The verbs.


@parsed.parsed
class Verb(parsed.Command):
    """A base: what every verb that reads the policy takes, the policy and how its result is written."""

    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {
        'select': Selected(),
        'max_bytes': Whole(least=0, what_it_is='a cap', label='bytes'),
    }

    policy: Path | None = parsed.Path(default=None, doc='the policy file (else $TOOLED_POLICY, else ./tooled.toml)')
    pretty: bool = parsed.Bool(default=False, doc='indent the JSON (the default on a terminal)')
    select: str | None = parsed.Str(default=None, doc='keep parts: a.b, list[].key; comma-separated')
    max_bytes: int | None = parsed.Int(
        default=None, doc='refuse a result over this many bytes (else $TOOLED_MAX_BYTES, else 28000; 0: none)'
    )


@parsed.parsed
class Written(Verb):
    """A base: a verb whose whole result may be written to a file."""

    out: Path | None = parsed.Path(default=None, doc='write the whole result to this file, uncapped, and say where')


@parsed.parsed
class Attaching(Written):
    """A base: a verb that runs in a session, when one is named."""

    session: str | None = parsed.Str(
        default=None, doc="run in this session: its name, or its socket's path (else $TOOLED_SESSION; empty: none)"
    )


@parsed.parsed
class Check(Written):
    """Check the policy and its protocol, and summarize what it shows.

    Load the policy and its protocol, resolve what it injects, and summarize what it shows.
    """

    PHRASE = 'check'
    EFFECT = parsed.Effect.READ

    connect: bool = parsed.Bool(default=False, doc='also open the session the policy describes')
    no_resolve: bool = parsed.Bool(
        default=False, doc='read no inject source: check the policy against its protocol without its secrets, as in CI'
    )


@parsed.parsed
class Operations(Attaching):
    """List the operations the policy shows.

    Every operation the policy shows, one line of each.
    """

    PHRASE = 'operations'
    EFFECT = parsed.Effect.READ


@parsed.parsed
class Describe(Attaching):
    """Describe one operation: its slots, and the schemas of what it takes and gives.

    One operation whole: its slots, and the JSON Schemas of what it takes and gives. --variant and --reply are one or
    the other.
    """

    PHRASE = 'describe {operation}'
    EFFECT = parsed.Effect.READ

    operation: str = parsed.Str(required=True, doc='an operation: its class name, or module:Class')
    variant: str | None = parsed.Str(default=None, doc="one variant of the request's union, by its tag")
    reply: bool = parsed.Bool(default=False, doc="the reply's whole schema, not its field list")


@parsed.parsed
class Reading(Attaching):
    """A base: a verb that acts on a contract, narrowed by its slots."""

    contract: str = parsed.Str(required=True, doc='a contract: its class name, or module:Class')
    slots: list[str] = parsed.Str(
        many=True, default_factory=list, doc='a slot to narrow by, slot=value; left out, it widens'
    )


@parsed.parsed
class Fetch(Reading):
    """Read a retained contract's values.

    A retained contract's values: those the slots select, or every one.
    """

    PHRASE = 'fetch {contract} {slots}'
    EFFECT = parsed.Effect.READ
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'timeout': SECONDS}

    timeout: float = parsed.Float(default=ASKED, doc='seconds to wait (default 5)')


@parsed.parsed
class Query(Reading):
    """Ask a read queryable.

    Ask a queryable that declares EFFECT = READ, and print its replies.
    """

    PHRASE = 'query {contract} {slots}'
    EFFECT = parsed.Effect.READ
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'timeout': SECONDS}

    timeout: float = parsed.Float(default=ASKED, doc='seconds to wait (default 5)')
    request: str | None = parsed.Str(default=None, doc='the request, in its wire form: JSON, - for stdin, @path')
    param: list[str] = parsed.Str(many=True, default_factory=list, doc='a selector parameter, k=v')


@parsed.parsed
class Listen(Verb):
    """Stream a retained contract or a stream, one JSON line per value.

    Each value of a retained contract or a stream as it arrives, one JSON line each (--pretty has no effect): a
    retained contract's current values first, then live ones.
    """

    PHRASE = 'listen {contract} {slots}'
    EFFECT = parsed.Effect.READ
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'count': Whole(), 'duration': DURATION}

    contract: str = parsed.Str(required=True, doc='a contract: its class name, or module:Class')
    slots: list[str] = parsed.Str(
        many=True, default_factory=list, doc='a slot to narrow by, slot=value; left out, it widens'
    )
    session: str | None = parsed.Str(
        default=None, doc="run in this session: its name, or its socket's path (else $TOOLED_SESSION; empty: none)"
    )
    count: int | None = parsed.Int(default=None, doc='stop after this many values')
    duration: float | None = parsed.Float(default=None, data_key='for', doc='stop after this long: 30, 30s, 5m, 1h')
    meta: bool = parsed.Bool(default=False, doc="add each value's key, origin and time")


@parsed.parsed
class Invoke(Reading):
    """Ask a queryable that writes.

    Ask a queryable that writes (EFFECT = WRITE or ACTION, or none declared), and print its replies. A refusal the
    server sends back is a reply. A write reaches one key, so each slot is given or bound. --dry-run builds the
    request and checks it, and prints it without sending; injected secrets show as <injected>.
    """

    PHRASE = 'invoke {contract} {slots}'
    EFFECT = parsed.Effect.WRITE
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'timeout': SECONDS}

    timeout: float = parsed.Float(default=ASKED, doc='seconds to wait (default 5)')
    request: str | None = parsed.Str(default=None, doc='the request, in its wire form: JSON, - for stdin, @path')
    no_retry: bool = parsed.Bool(
        default=False,
        doc='after no reply, do not ask once more (a request with a generated id is asked twice by default)',
    )


@parsed.parsed
class Wait(Reading):
    """Wait for a retained contract or a stream to hold a value, and print it.

    Subscribe to a retained contract or a stream until a value meets every --until, and print it: a retained
    contract already in that state answers at once. Past --timeout, exit 7 with the last value seen.
    """

    PHRASE = 'wait {contract} {slots}'
    EFFECT = parsed.Effect.READ
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'until': Condition(), 'timeout': SECONDS}

    until: list[str] = parsed.Str(
        many=True,
        required=True,
        doc='a dotted path and the value it must equal, read as JSON if it can be (40, true), else as a string; '
        'repeat for several, all required',
    )
    timeout: float = parsed.Float(default=WAITED, doc='seconds to wait (default 30)')
    meta: bool = parsed.Bool(default=False, doc="add the value's key, origin and time")


@parsed.parsed
class Tools(Attaching):
    """Export the operations the policy shows as tool definitions, for a harness.

    The operations the policy shows, as tool definitions a harness hands a model: one for each fetch, query, write,
    wait or listen, and one for each allowed variant of a request's union, in a schema dialect a constrained decoder
    holds. Sorted, and byte-stable.
    """

    PHRASE = 'tools'
    EFFECT = parsed.Effect.READ
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'format': Form(), 'verbs': Picks()}

    format: str = parsed.Str(default='openai', doc='the form to write them in (default openai)')
    verbs: str | None = parsed.Str(
        default=None,
        doc='only these tools, comma-separated: a verb for all of its, or verb:GLOB for those whose operation the '
        'glob matches (wait:Fleet*); default all',
    )
    sizes: bool = parsed.Bool(default=False, doc="each tool's bytes and estimated tokens, not the tools")


@parsed.parsed
class Call(Attaching):
    """Carry out one tool call, as a model wrote it.

    Carry out one call of a tool `tooled tools` exported: its arguments mapped back to the verb its name begins
    with, then checked and carried out as that verb is. Errors are worded for the model.
    """

    PHRASE = 'call {tool}'
    EFFECT = parsed.Effect.WRITE

    tool: str = parsed.Str(required=True, doc='the tool, by the name `tooled tools` gives it')
    args: str | None = parsed.Str(
        default=None, doc="the call's arguments: a JSON object, - for stdin, @path (default {})"
    )


@parsed.parsed
class Lined(Attaching):
    """A base: a verb that runs a line of the policy's app, its words after tooled's own options."""

    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'line': LINE, 'timeout': DURATION}

    line: str = parsed.Str(required=True, doc="the app's line, its flags among it: --yes, --until, --watch")
    timeout: float = parsed.Float(
        default=LINE_BOUND,
        doc="how long to wait on the line: 30, 30s, 5m (default 30s; a line's own wait, its bound and 5s)",
    )


@parsed.parsed
class See(Lined):
    """Run a line of the app's: a read, a command that declares EFFECT = READ.

    Run a line of the policy's app, a read: tooled's own options first, then the line's words, each as the shell
    gave it, or the whole line as one. The line is judged by the policy before it runs, and parsed's envelope
    printed.
    """

    PHRASE = 'see {line**}'
    EFFECT = parsed.Effect.READ


@parsed.parsed
class Do(Lined):
    """Run a line of the app's: any other command.

    Run a line of the policy's app, any command but a read: tooled's own options first, then the line's words, each
    as the shell gave it, or the whole line as one. The line is judged by the policy before it runs, and parsed's
    envelope printed. --yes on the line answers its questions where the policy allows confirm.
    """

    PHRASE = 'do {line**}'
    EFFECT = parsed.Effect.ACTION


@parsed.parsed
class Serve(parsed.Command):
    """Serve the tools the policy shows over MCP, on stdio, attached to a session.

    Serve the tools the policy shows over MCP on stdin and stdout, for a harness that takes tools natively. Each call
    runs in the session --session names (else $TOOLED_SESSION), beside the command line's; with none named, in one of
    its own, opened from the policy and closed when serve ends.
    """

    PHRASE = 'serve'
    EFFECT = parsed.Effect.ACTION
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {
        'verbs': Picks(),
        'idle': Duration(never=True),
        'timeout': SECONDS,
    }

    session: str | None = parsed.Str(
        default=None, doc="the session to attach to: its name, or its socket's path (else $TOOLED_SESSION)"
    )
    verbs: str | None = parsed.Str(
        default=None, doc='only these tools, as tools --verbs picks them (wait:Fleet*); default all'
    )
    policy: Path | None = parsed.Path(
        default=None, doc='the policy, for a session of its own (else $TOOLED_POLICY, else ./tooled.toml)'
    )
    name: str | None = parsed.Str(default=None, doc='the name of a session of its own; made up when not given')
    idle: float | None = parsed.Float(
        default=None, doc='end a session of its own after this long with no call: 30m, 1h, 0 for never (never)'
    )
    timeout: float = parsed.Float(default=WAIT, doc='seconds to wait on the session (default 30)')


# session's verbs, mounted under `session`.


@parsed.parsed
class Waiting(parsed.Command):
    """A base: a session's verb, which waits on the session."""

    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'timeout': SECONDS}

    timeout: float = parsed.Float(default=WAIT, doc='seconds to wait on the session (default 30)')


@parsed.parsed
class Opening(Waiting):
    """A base: a session's verb that opens one."""

    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'idle': Duration(never=True)}

    policy: Path | None = parsed.Path(default=None, doc='the policy file (else $TOOLED_POLICY, else ./tooled.toml)')
    name: str | None = parsed.Str(
        default=None, doc="its name, or its socket's path (holding /); made up when not given"
    )
    idle: float | None = parsed.Float(
        default=None,
        doc='end it after this long with no call: 30m, 1h, 0 for never (default 30m; never, tied to a process)',
    )


@parsed.parsed
class SessionStart(Opening):
    """Open a session, and say where it is.

    Open a session in a process of its own, wait until it is ready, and say where it is.
    """

    PHRASE = 'start'
    EFFECT = parsed.Effect.ACTION
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'tied': Process()}

    pretty: bool = parsed.Bool(default=False, doc='indent the JSON (the default on a terminal)')
    tied: int | None = parsed.Int(default=None, data_key='for', doc='end it when this process ends')
    foreground: bool = parsed.Bool(default=False, doc='hold it in this process until it ends, for a supervisor')


@parsed.parsed
class SessionRun(Opening):
    """Run a command in a session of its own, closed when it ends.

    Open a session, run the command with $TOOLED_SESSION naming it, and close the session when the command ends,
    however it ends; the exit code is the command's. The command's words follow the session's options, after --
    where one begins with a dash.
    """

    PHRASE = 'run {command**}'
    EFFECT = parsed.Effect.ACTION
    KINDS: ClassVar[Mapping[str, parsed.Kind]] = {'command': LINE}

    command: str = parsed.Str(required=True, doc='the command, and its arguments')


@parsed.parsed
class SessionStop(Waiting):
    """Close a session, its state left."""

    PHRASE = 'stop {name}'
    EFFECT = parsed.Effect.ACTION

    name: str | None = parsed.Str(default=None, doc='its name, or its socket path (else $TOOLED_SESSION)')
    pretty: bool = parsed.Bool(default=False, doc='indent the JSON (the default on a terminal)')


@parsed.parsed
class SessionStatus(Waiting):
    """Say how a session is, or every one's."""

    PHRASE = 'status {name}'
    EFFECT = parsed.Effect.READ

    name: str | None = parsed.Str(default=None, doc='its name, or its socket path (else $TOOLED_SESSION)')
    pretty: bool = parsed.Bool(default=False, doc='indent the JSON (the default on a terminal)')


#: The verbs, in the order ``tooled --help`` lists them.
COMMANDS: tuple[type[parsed.Command], ...] = (
    Check,
    Operations,
    Describe,
    Fetch,
    Query,
    Listen,
    Invoke,
    Wait,
    Tools,
    Call,
    See,
    Do,
    Serve,
)

#: session's verbs.
SESSION: tuple[type[parsed.Command], ...] = (SessionStart, SessionRun, SessionStop, SessionStatus)

#: What ``tooled --help`` lists for ``session``.
SESSION_DOC = "Hold the policy's world open in a session, for the calls that name it."


@functools.cache
def app() -> parsed.App:
    """The command line, as a parsed app: tooled's verbs at its root, and ``session``'s under ``session``."""
    from . import __version__  # lazy: the package imports the CLI before it sets its version

    tooled = parsed.App('tooled', version=__version__)
    tooled.mount(parsed.Tree(list(COMMANDS)))
    tooled.mount(parsed.Tree(list(SESSION)), under='session', doc=SESSION_DOC)
    return tooled


@dataclass(frozen=True, slots=True)
class Given:
    """A program's arguments, read.

    Attributes:
        command: The verb's command, loaded; ``None`` where the arguments ask for help or the version.
        own: parsed's own flags given the verb (``yes``, ``dry-run``, ``json``), by name; help aside.
        help: The help the arguments ask for, written; ``None`` for none.
        version: Whether they ask for the program's version.
    """

    command: parsed.Command | None
    own: frozenset[str] = frozenset()
    help: str | None = None
    version: bool = False

    @property
    def line(self) -> str:
        """A line's verb's line, as the app is to read it: one given as one argument its words, parsed's own put back.

        Raises:
            TypeError: The verb takes no line.
        """
        if not isinstance(self.command, Lined):
            msg = f'{type(self.command).__name__} takes no line'
            raise TypeError(msg)
        line = self.command.line
        words = args_of(line)
        if len(words) == 1:  # a single argument is the whole line, as parsed's command line takes one
            line = words[0]
        return ' '.join([*(f'--{name}' for name in PASSED_ON if name in self.own), line] if line else [])

    @property
    def words(self) -> list[str]:
        """``session run``'s command, as the arguments of the process it runs.

        Raises:
            TypeError: The verb runs no command.
        """
        if not isinstance(self.command, SessionRun):
            msg = f'{type(self.command).__name__} runs no command'
            raise TypeError(msg)
        return args_of(self.command.command)


def read(argv: Sequence[str]) -> Given:
    """A program's arguments, read: the root options (``--version``), then the verb's line, by tooled's app.

    ``--help`` (or ``-h``) anywhere among a verb's own words gives the verb's help, or what a line short of one may
    still say; with no words, the program's.

    Raises:
        UsageError: They don't read: a verb tooled doesn't have, a flag it doesn't take, a value it can't read. The
            message is parsed's refusal, its usage after it.
    """
    tooled = app()
    try:
        said = read_args(list(argv), options_of('tooled', None, tooled.version))
    except parsed.ReadError as exc:
        raise _refused(exc) from exc
    if said.version:
        return Given(None, version=True)
    if not said.line.strip():
        if _HELP in said.own:
            return Given(None, help=program_help())
        msg = 'tooled takes a verb, then its words: tooled --help lists them'
        raise UsageError(msg)
    line = said.run
    try:
        routed = tooled.walk(line)
    except parsed.ReadError as exc:
        if _HELP in said.own or {'-h', '--help'} & set(argv):
            return Given(None, help=_help(line))
        raise _refused(exc) from exc
    own = frozenset(routed.reading.own)
    if _HELP in own:
        return Given(None, help=_help(line))
    try:
        command = routed.load()
    except parsed.ReadError as exc:
        raise _refused(exc) from exc
    return Given(command, own=_owned(command, own))


def _owned(command: parsed.Command, own: frozenset[str]) -> frozenset[str]:
    """The flags of parsed's own given ``command``, once each is one its verb takes.

    Raises:
        UsageError: One its verb doesn't take: only a line's verb takes them, to pass on, and ``invoke`` ``--dry-run``.
    """
    takes = set(PASSED_ON) if isinstance(command, Lined) else {'dry-run'} if isinstance(command, Invoke) else set()
    extra = sorted(own - takes)
    if extra:
        msg = f'tooled {verb_of(type(command))} takes no --{extra[0]}'
        raise UsageError(msg)
    return own


def verb_of(cls: type[parsed.Command]) -> str:
    """The verb a command class is, as a line says it: ``fetch``, ``session start``."""
    word = (cls.PHRASE or '').split()[0]
    return f'session {word}' if cls in SESSION else word


def _help(line: str) -> str:
    """The help ``line`` asks for: a verb's, or what a line short of one may still say.

    Raises:
        UsageError: The line's words go nowhere.
    """
    try:
        return app().help(line)
    except parsed.ReadError as exc:
        raise _refused(exc) from exc


def program_help() -> str:
    """``tooled --help``: what tooled is, how it's said, its verbs each by its word and summary, and how output reads.

    The verbs are listed by their words alone, since each takes the policy's flags and a listing of usages would say
    them for every one; ``tooled VERB --help`` says a verb's.
    """
    from parsed.help import rows, summary

    usage = options_of('tooled', None, app().version).help()
    listed = [*((verb_of(cls), summary(cls) or '') for cls in COMMANDS), ('session …', SESSION_DOC)]
    said = [DESCRIPTION, usage, '\n'.join(rows(listed)), 'tooled VERB --help says what a verb takes.', EPILOG.rstrip()]
    return '\n\n'.join(said) + '\n'


def _refused(exc: parsed.ReadError) -> UsageError:
    """A refusal of parsed's as tooled's usage error, its message parsed's."""
    return UsageError(str(exc))
