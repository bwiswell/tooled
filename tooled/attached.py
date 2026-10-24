"""What a call attached to a session brings: its own streams, working directory and variables.

In a session (:mod:`tooled.server`), calls run side by side in one process, so what a cold call takes from its
process (``sys.stdout``, the working directory, ``os.environ``) each takes from its own :class:`Call` instead. The
server sets the call in a context variable, in the thread that runs it, and a task the call starts on the session's
loop inherits it. Outside a session there's no call, and each function here answers as the process would.

The session's process routes its standard streams by call (:func:`install`): what a call writes to ``sys.stdout``
goes back to that call's client, and what's written outside any call goes to the process's own (its log).

It imports nothing but the standard library, as the call that attaches imports no more.
"""

from __future__ import annotations

import contextlib
import io
import os
import signal
import sys
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping


class Stop(threading.Event):
    """A call's stop: set when its client asks it to stop, by a signal's number, or goes."""

    def __init__(self) -> None:
        super().__init__()
        self.signum = int(signal.SIGTERM)

    def by(self, signum: int) -> None:
        """Set it, for ``signum``: the first one given stands."""
        if not self.is_set():
            self.signum = int(signum)
        self.set()


@dataclass(slots=True, eq=False)
class Call:
    """One attached call's own.

    Attributes:
        out: Where its stdout goes.
        err: Where its stderr goes.
        stdin: What its stdin reads: what the client sent, when the call reads it (``--args -``), else nothing.
        cwd: Its working directory, against which a relative path it gives is read.
        environ: Its ``TOOLED_*`` variables.
        tty: Whether its stdout is a terminal.
        stop: Set when its client asks it to stop, or goes.
        held: The session it runs in (:class:`tooled.server.Held`).
    """

    out: Any  # a text stream
    err: Any
    stdin: Any
    cwd: str
    environ: Mapping[str, str]
    tty: bool = False
    stop: Stop = field(default_factory=Stop)
    held: Any = None


#: The call running here, in a session; ``None`` outside one.
CALL: ContextVar[Call | None] = ContextVar('tooled_call', default=None)


class Stopped(BaseException):
    """A call its client stopped, or left, before it ended: it ends with no result, as a cold one killed does."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def current() -> Call | None:
    """The call running here, or ``None``."""
    return CALL.get()


def held() -> Any:  # a tooled.server.Held, or None
    """The session the call running here runs in; ``None`` for a cold call."""
    call = CALL.get()
    return call.held if call is not None else None


def environ() -> Mapping[str, str]:
    """The call's variables, in a session; the process's, otherwise."""
    call = CALL.get()
    return call.environ if call is not None else os.environ


def path(text: str) -> Path:
    """``text`` as a path, ``~`` expanded: in a session, a relative one is read against the call's directory."""
    found = Path(text).expanduser()
    call = CALL.get()
    if call is None or found.is_absolute():
        return found
    return Path(call.cwd) / found


def stop() -> Stop:
    """The call's stop, in a session; a new one, for a cold call's own signals to set."""
    call = CALL.get()
    return call.stop if call is not None else Stop()


@contextlib.contextmanager
def kept(wrote: io.StringIO) -> Generator[None]:
    """While the block runs, the call's stdout is ``wrote``, and its stdin reads nothing: a line's handlers, kept apart.

    It's the session's :func:`tooled.tree._alone`: what a handler writes stays out of tooled's result, and a prompt
    fails at once.
    """
    call = CALL.get()
    if call is None:
        yield
        return
    token = CALL.set(replace(call, out=wrote, stdin=io.StringIO(''), tty=False))
    try:
        yield
    finally:
        CALL.reset(token)


class _Routed(io.TextIOBase):
    """A standard stream that each call has its own of: its call's, in one; the process's, outside any."""

    def __init__(self, kind: str, own: Any) -> None:  # own: the process's stream
        self.kind, self.own = kind, own

    def _here(self) -> Any:  # the stream this context writes to, or reads from
        call = CALL.get()
        return getattr(call, self.kind) if call is not None else self.own

    def write(self, text: str) -> int:
        """Write ``text`` to the call's stream."""
        return self._here().write(text)

    def flush(self) -> None:
        """Flush the call's stream."""
        self._here().flush()

    def read(self, size: int | None = -1) -> str:
        """Read from the call's stream."""
        return self._here().read(size)

    def readline(self, size: int | None = -1) -> str:
        """Read a line from the call's stream."""
        return self._here().readline(size)

    def isatty(self) -> bool:
        """Whether the call's stdout is a terminal (its client's); never its stdin or stderr."""
        call = CALL.get()
        if call is None:
            return bool(getattr(self.own, 'isatty', lambda: False)())
        return self.kind == 'out' and call.tty

    def close(self) -> None:
        """Nothing: the streams it routes to are their owners' to close."""

    def fileno(self) -> int:
        """The process's descriptor, outside a call; a call's stream has none."""
        if CALL.get() is not None:
            msg = 'fileno'
            raise io.UnsupportedOperation(msg)
        return self.own.fileno()


def install() -> Callable[[], None]:
    """Route ``sys.stdin``, ``sys.stdout`` and ``sys.stderr`` by call, for a session's process.

    Returns:
        What puts the streams back.
    """
    held = sys.stdin, sys.stdout, sys.stderr
    sys.stdin, sys.stdout, sys.stderr = (
        _Routed(kind, own) for kind, own in zip(('stdin', 'out', 'err'), held, strict=True)
    )

    def restore() -> None:
        sys.stdin, sys.stdout, sys.stderr = held

    return restore
