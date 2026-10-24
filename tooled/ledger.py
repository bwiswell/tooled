"""The call log: one JSON line a run of ``tooled``, appended to ``$TOOLED_LOG``, for a harness's ledger.

With ``TOOLED_LOG`` set to a path, every run that ends, done or with an error, appends one line:

```json
{"at": "2026-10-23T22:19:04.512Z", "verb": "call", "route": "wire", "session": null, "operation": "OrderResult",
 "tool": "invoke_order_result_jump", "variant": "jump", "arguments": ["fleet", "to"], "flags": ["--args", "--policy"],
 "exit": 0, "error": null, "path": null, "bytes": 61, "seconds": 0.84}
```

- ``at``: when the run began, UTC; ``seconds``: how long it took.
- ``verb``: the verb, ``null`` for one tooled doesn't have; ``route``: ``wire`` or ``tree``, for a verb on an
  operation; ``session``: the session it ran in, by the name or path the call gave, ``null`` for a cold call.
- ``operation``, ``tool``, ``variant``: what it acted on, as far as it got: a call's tool is the name it was given,
  found or not.
- ``arguments``: the names of what was given, a call's arguments or a verb's slots; ``flags``: the options given,
  by name.
- ``exit``, ``error``, ``path``: the exit code, the error's kind, and where in the input it lies.
- ``bytes``: what went to stdout, the result a harness hands back.

**It records no value:** no slot's, request's, argument's or reply's, and no error's message, which can quote one.
Every key is on every line, ``null`` where it doesn't apply, so a reader needs no defaults. A run cut short (a
crash, ``--help``, a kill) leaves no line, and a failure to write the line is a note on stderr that leaves the run's
exit code alone.

Each line is one append, so runs side by side (a seat's turn, a harness's tool calls) share a file. A harness that
keeps a record a turn gives each its own file: ``TOOLED_LOG=turns/<stamp>/calls.jsonl``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .errors import TooledError

#: The environment variable naming the call log.
LOG_ENV = 'TOOLED_LOG'


@dataclass
class Entry:
    """One run's line, filled in as the run learns it."""

    at: str = ''
    verb: str | None = None
    route: str | None = None
    session: str | None = None
    operation: str | None = None
    tool: str | None = None
    variant: str | None = None
    arguments: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    exit: int | None = None
    error: str | None = None
    path: list[str | int] | None = None
    bytes: int = 0
    seconds: float = 0.0
    _began: float = field(default=0.0, repr=False)

    @classmethod
    def begin(cls, argv: Iterable[str], verbs: Iterable[str]) -> Entry:
        """A run's entry, from its arguments: the verb, when it's one of ``verbs``, and the options given, by name."""
        given = list(argv)
        known = set(verbs)
        stamp = datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        entry = cls(at=stamp, verb=given[0] if given and given[0] in known else None, _began=time.monotonic())
        entry.flags = list(dict.fromkeys(arg.partition('=')[0] for arg in given if arg.startswith('--')))
        return entry

    def acted_on(self, operation: str, *, variant: str | None = None, route: str = 'wire') -> None:
        """The operation a verb acts on, and its route; and the variant its request carries, once it's known."""
        self.route, self.operation = route, operation
        if variant is not None:
            self.variant = variant

    def took(self, line: Mapping[str, Any]) -> None:
        """Take what a session's line of this run says it acted on, how it ended, and what it wrote."""
        for key in ('route', 'operation', 'tool', 'variant', 'arguments', 'error', 'path', 'bytes'):
            if key in line:
                setattr(self, key, line[key])

    def wrote(self, text: str) -> None:
        """Count ``text``, as written to stdout."""
        self.bytes += len(text.encode())

    def end(self, code: int, error: TooledError | None = None) -> None:
        """The run's end: its exit code, and its error's kind and path."""
        self.exit, self.seconds = code, round(time.monotonic() - self._began, 3)
        if error is not None:
            self.error, self.path = error.kind, list(error.path) or None

    def as_json(self) -> dict[str, Any]:
        """The line, every key in its place."""
        return {key: value for key, value in asdict(self).items() if not key.startswith('_')}


def append(entry: Entry, environ: Mapping[str, str] | None = None) -> str | None:
    """Append ``entry`` to the call log, when ``$TOOLED_LOG`` names one.

    Returns:
        A note saying why it couldn't be written, or ``None``.
    """
    target = (os.environ if environ is None else environ).get(LOG_ENV)
    if not target:
        return None
    line = json.dumps(entry.as_json(), ensure_ascii=False) + '\n'
    try:
        with Path(target).expanduser().open('a', encoding='utf-8') as log:
            log.write(line)
    except OSError as exc:
        return f'could not write the call log to {target}: {exc.strerror or exc}'
    return None
