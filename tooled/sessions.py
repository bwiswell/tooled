"""Sessions: a policy's world held open in a process of its own, for the calls that name it.

A session (:mod:`tooled.server`) holds, from its start, the policy as it was loaded, its catalog and rules, the
values it injects, the mesh, open, and the app, open on its state. A call that attaches to it loads and opens none
of that, so it costs about what starting ``tooled`` does. Many sessions run side by side, on one policy or several,
each with its own state and injected values: a harness exports ``tools`` once, and each agent's calls reach its own
session.

**Naming one.** A session is named by a name (letters, digits, ``.``, ``_`` and ``-``, up to 64) or by its socket's
path (a value holding ``/``). A name's socket is ``$XDG_RUNTIME_DIR/tooled/<name>.sock`` (``$TMPDIR/tooled-<uid>/``
without the variable), beside its status (``<name>.json``) and its log (``<name>.log``), in a directory that must be
the user's alone; a path's are beside it (``<path>.json``, ``<path>.log``).

**Attaching.** A call attaches when given ``--session``, else ``$TOOLED_SESSION`` (empty: none), and only then: no
call finds a session by its policy or its directory. Every verb but ``check`` and ``session``'s own attaches
(:data:`ATTACHES`). The call sends its arguments, its working directory, its ``TOOLED_*`` variables, whether its
stdout is a terminal, and its stdin when it reads it (``--args -``), and writes what comes back as it comes: the same
stdout, stderr and exit code as the call made cold. A signal asks the session to stop the call (a watch ends as it
would), a second ends the client at once. A session named but not open is refused, exit 5, saying why it ended when
it knows, never quietly run cold: whoever started it believes its state is held.

**The call log.** The session hands back the call's line, and the client appends it to its own ``$TOOLED_LOG``,
naming the session (:mod:`tooled.ledger`).

**The protocol** is tooled's own, client to server: JSON lines over the socket. A call sends ``{"tooled": version,
"argv", "cwd", "env", "tty", "stdin"}``, then ``{"stop": signum}`` if it's asked to stop; the session sends
``{"out": text}`` and ``{"err": text}`` as the call writes, then ``{"end": code, "entry": line}``, or ``{"end": code,
"cut": true}`` for a run cut short (``--help``), which leaves no line. ``{"control": "status"}`` and ``{"control":
"stop"}`` ask the session itself.

This module imports nothing but the standard library, until a verb of its own runs.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import signal
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from .errors import MeshError, TooledError, UsageError

if TYPE_CHECKING:
    import argparse
    import subprocess
    from collections.abc import Callable, Generator, Mapping

    from .ledger import Entry

#: The environment variable naming the session a call attaches to.
SESSION_ENV = 'TOOLED_SESSION'

#: The verbs that attach to a session, when one is named: every one but ``check`` and ``session``'s own.
ATTACHES = frozenset(
    {'operations', 'describe', 'fetch', 'query', 'listen', 'invoke', 'wait', 'tools', 'call', 'see', 'do'}
)

#: A session's idle timeout, in seconds, when it's tied to no process and none is given.
IDLE = 30 * 60.0

#: How long ``session start`` and ``session stop`` wait on the session, in seconds, when no ``--timeout`` is given.
WAIT = 30.0

#: What a session's name may be.
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')

#: The longest socket path a unix socket takes, in bytes (macOS's; Linux's is a few more).
_SOCKET_MAX = 103

#: The verbs that read a request from stdin, given ``-``, and the options that take it.
_READS_STDIN = {'call': ('--args',), 'query': ('--request',), 'invoke': ('--request',)}


@dataclass(frozen=True, slots=True)
class Where:
    """Where a session lives: its socket, its status and its log.

    Attributes:
        handle: How a call names it: its name, or its socket's absolute path.
        socket: Its socket.
        status: Its status file: open, or how it ended.
        log: What its process writes outside any call.
    """

    handle: str
    socket: Path
    status: Path
    log: Path

    @classmethod
    def of(cls, handle: str) -> Where:
        """Where the session ``handle`` names lives: a name's under the runtime directory, a path's beside it.

        Raises:
            UsageError: ``handle`` is neither a name nor a path, or its socket's path is too long for one.
        """
        if '/' in handle:
            found = Path(handle).expanduser().absolute()
            where = cls(str(found), found, found.with_name(found.name + '.json'), found.with_name(found.name + '.log'))
        elif _NAME.fullmatch(handle):
            base = runtime_dir()
            where = cls(handle, base / f'{handle}.sock', base / f'{handle}.json', base / f'{handle}.log')
        else:
            msg = (
                f'a session is named by letters, digits, ".", "_" and "-" (up to 64, a letter or digit first), or by '
                f'its socket\'s path (holding "/"); not {handle!r}'
            )
            raise UsageError(msg)
        if len(os.fsencode(where.socket)) > _SOCKET_MAX:
            msg = f'the socket path {where.socket} is over {_SOCKET_MAX} bytes, too long for a unix socket'
            raise UsageError(msg)
        return where

    def record(self) -> dict[str, Any] | None:
        """What its status file says, or ``None`` when there's none, or it can't be read."""
        try:
            found = json.loads(self.status.read_text(encoding='utf-8'))
        except OSError, ValueError:
            return None
        return found if isinstance(found, dict) else None

    def gone(self) -> str:
        """Why no session is open here: how it ended, when its status file says."""
        found = self.record()
        if found is not None and found.get('open') is False:
            return f'no session {self.handle!r} is open: it ended at {found.get("ended")}: {found.get("reason")}'
        return f'no session {self.handle!r} is open: tooled session start opens one'


def runtime_dir() -> Path:
    """The directory a named session lives in, made the user's alone if it isn't there.

    Raises:
        UsageError: It's there, and isn't the user's alone.
    """
    base = os.environ.get('XDG_RUNTIME_DIR')
    found = Path(base) / 'tooled' if base else Path(tempfile.gettempdir()) / f'tooled-{os.getuid()}'
    found.mkdir(mode=0o700, parents=True, exist_ok=True)
    held = found.stat()
    if held.st_uid != os.getuid() or held.st_mode & 0o077:
        msg = f"{found} holds sessions, and must be the user's alone (theirs, mode 0700), which it isn't"
        raise UsageError(msg)
    return found


def connected(where: Where) -> socket.socket:
    """A connection to the session at ``where``.

    Raises:
        MeshError: None is open there: why it ended, when known.
    """
    link = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        link.connect(str(where.socket))
    except OSError as exc:
        link.close()
        raise MeshError(where.gone()) from exc
    return link


def send(link: socket.socket, frame: Mapping[str, Any]) -> None:
    """Send ``frame`` as one JSON line."""
    link.sendall((json.dumps(frame, ensure_ascii=False) + '\n').encode())


@dataclass(frozen=True, slots=True)
class Ended:
    """How a call run in a session ended.

    Attributes:
        code: Its exit code.
        line: The session's line for the call log; ``None`` for a run cut short (``--help``), which leaves none.
    """

    code: int
    line: dict[str, Any] | None


class Exchange:
    """One call run in a session, from its client's side: sent as the block opens, its frames read by :meth:`ended`.

    Args:
        where: The session.
        given: The call's arguments, as a command line gives them.
        environ: Its ``TOOLED_*`` variables.
        cwd: Its working directory.
        tty: Whether its stdout is a terminal.
        stdin: Its stdin, when its arguments read it; ``None`` otherwise.

    Raises:
        MeshError: No session is open there (on opening), or it ended during the call (in :meth:`ended`).
    """

    def __init__(  # noqa: PLR0913 -- one call: its arguments, and what it brings of its own
        self, where: Where, given: list[str], *, environ: Mapping[str, str], cwd: str, tty: bool, stdin: str | None
    ) -> None:
        from . import __version__

        self.where = where
        self.request = {
            'tooled': __version__,
            'argv': given,
            'cwd': cwd,
            'env': dict(environ),
            'tty': tty,
            'stdin': stdin,
        }

    def __enter__(self) -> Self:
        """Connect, and send the call."""
        self.link = connected(self.where)
        self.lines = self.link.makefile('r', encoding='utf-8', newline='\n')
        with contextlib.suppress(OSError):  # closed on us (a refusal, an ending session): what it said is still there
            send(self.link, self.request)
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the connection."""
        self.lines.close()
        self.link.close()

    def stop(self, signum: int) -> None:
        """Ask the session to stop the call, for ``signum``: a watch ends as a signal ends one; another is cut short."""
        with contextlib.suppress(OSError):  # the session is gone: the frames say so
            send(self.link, {'stop': signum})

    def ended(self, *, out: Callable[[str], object], err: Callable[[str], object]) -> Ended:
        """Each frame's text handed to ``out`` or ``err`` as it comes, then how the call ended.

        Raises:
            MeshError: The session ended during the call.
        """
        for line in self.lines:
            frame = json.loads(line)
            if 'out' in frame:
                out(frame['out'])
            elif 'err' in frame:
                err(frame['err'])
            elif 'end' in frame:
                return Ended(frame['end'], None if frame.get('cut') else frame['entry'])
        msg = f'the session {self.where.handle!r} ended during the call; {self.where.gone()}'
        raise MeshError(msg)


def own_variables() -> dict[str, str]:
    """This process's ``TOOLED_*`` variables, which a call brings to a session."""
    return {name: value for name, value in os.environ.items() if name.startswith('TOOLED_')}


def attach(given: list[str], handle: str, entry: Entry) -> int:
    """Run ``given`` in the session ``handle`` names: its output written as it comes, its exit code returned.

    ``entry`` ends as the session ended it, with this call's own time. A failure to reach the session is written as
    tooled writes any, and ends ``entry``.

    Raises:
        SystemExit: The session cut the run short, as ``--help`` does a cold one: there's no line to log.
    """
    entry.session = handle
    try:
        where = Where.of(handle)
        calling = Exchange(
            where,
            given,
            environ=own_variables(),
            cwd=str(Path.cwd()),
            tty=sys.stdout.isatty(),
            stdin=_stdin_of(given),
        )
        with calling as call, _stopping(call):
            ended = call.ended(out=_written(sys.stdout), err=_written(sys.stderr))
    except TooledError as exc:
        return failed(exc, entry)
    if ended.line is None:
        raise SystemExit(ended.code)
    entry.took(ended.line)
    entry.end(ended.code)
    return ended.code


def _written(stream: Any) -> Callable[[str], None]:  # a text stream
    """What writes a frame's text to ``stream`` at once."""

    def write(text: str) -> None:
        stream.write(text)
        stream.flush()

    return write


def failed(exc: TooledError, entry: Entry) -> int:
    """Write ``exc`` as tooled writes an error, end ``entry`` with it, and return its exit code."""
    sys.stderr.write(json.dumps(exc.to_json()) + '\n')
    entry.end(exc.exit_code, exc)
    return exc.exit_code


def _stdin_of(given: list[str]) -> str | None:
    """The call's stdin, when its arguments read it (``--args -``); ``None``, read from nothing, when they don't."""
    options = _READS_STDIN.get(given[0], ())
    for at, word in enumerate(given):
        if (word in options and given[at + 1 : at + 2] == ['-']) or word in {f'{option}=-' for option in options}:
            return sys.stdin.read()
    return None


@contextlib.contextmanager
def _stopping(call: Exchange) -> Generator[None]:
    """While the call runs: a first SIGINT or SIGTERM asks the session to stop it, a second ends this client at once.

    Only on the main thread; elsewhere, the program's own handling stands.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    asked: list[int] = []

    def stop(signum: int, _frame: object) -> None:
        if asked:
            raise SystemExit(128 + signum)
        asked.append(signum)
        call.stop(signum)

    held = {signum: signal.signal(signum, stop) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for signum, handler in held.items():
            signal.signal(signum, handler)


def asked(where: Where, request: Mapping[str, Any], *, until: str, timeout: float) -> dict[str, Any]:
    """Ask the session at ``where`` itself, and wait for the reply that holds ``until``.

    Raises:
        MeshError: None is open there, or it didn't reply within ``timeout`` seconds.
    """
    with connected(where) as link, link.makefile('r', encoding='utf-8', newline='\n') as lines:
        link.settimeout(timeout)
        try:
            send(link, request)
            for line in lines:
                frame = json.loads(line)
                if until in frame:
                    return frame[until]
        except TimeoutError as exc:
            msg = f'the session {where.handle!r} did not reply within {timeout:g}s'
            raise MeshError(msg) from exc
    msg = f'the session {where.handle!r} closed without replying'
    raise MeshError(msg)


def start(args: argparse.Namespace) -> int:
    """``session start``: open a session, and say where it is; with ``--foreground``, hold it here until it ends."""
    from .cli import emit

    if args.foreground:
        return _foreground(args)
    emit(started(args, tied=args.tied), args)
    return 0


def run(args: argparse.Namespace) -> int:
    """``session run``: a session for a command, named in its ``$TOOLED_SESSION``, closed when it ends.

    The session is tied to this process, so it ends with it, however it ends. The exit code is the command's.

    Raises:
        UsageError: There's no command, or it can't be run.
    """
    import subprocess

    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        msg = 'tooled session run takes a command, after --: tooled session run -- COMMAND …'
        raise UsageError(msg)
    summary = started(args, tied=os.getpid())
    where = Where.of(summary['session'])
    try:
        child = subprocess.Popen(command, env={**os.environ, SESSION_ENV: where.handle})  # noqa: S603 -- the user's own
    except OSError as exc:
        stopped(where, timeout=args.timeout)
        msg = f'could not run {command[0]!r}: {exc.strerror or exc}'
        raise UsageError(msg) from exc
    with _forwarded(child):
        code = child.wait()
    with contextlib.suppress(MeshError):  # already ended: its idle timeout, a wedged loop
        stopped(where, timeout=args.timeout)
    return code


def stop(args: argparse.Namespace) -> int:
    """``session stop``: close a session, its state left, and say how it ended."""
    from .cli import emit

    emit(stopped(Where.of(_named(args.name)), timeout=args.timeout), args)
    return 0


def status(args: argparse.Namespace) -> int:
    """``session status``: one session's, or, naming none, every one under the runtime directory.

    Raises:
        MeshError: The one named isn't open.
    """
    from .cli import emit

    handle = args.name or os.environ.get(SESSION_ENV)
    if handle:
        emit(asked(Where.of(handle), {'control': 'status'}, until='status', timeout=args.timeout), args)
    else:
        emit(_listing(timeout=args.timeout), args)
    return 0


def _named(given: str | None) -> str:
    """The session a verb names: ``given``, else ``$TOOLED_SESSION``.

    Raises:
        UsageError: Neither names one.
    """
    handle = given or os.environ.get(SESSION_ENV)
    if not handle:
        msg = f'which session: name one, or set ${SESSION_ENV}'
        raise UsageError(msg)
    return handle


def stopped(where: Where, *, timeout: float) -> dict[str, Any]:
    """Ask the session at ``where`` to close, and wait until it has: how it ended."""
    return asked(where, {'control': 'stop'}, until='stopped', timeout=timeout)


def started(args: argparse.Namespace, *, tied: int | None) -> dict[str, Any]:
    """Open a session in a process of its own, and wait until it's ready: where it is, and what it holds.

    The process starts in this one's environment and directory, and imports what this one can.

    Raises:
        MeshError: It didn't open, or not within ``--timeout``.
        TooledError: What opening it raised: the policy's error, its app's.
    """
    import subprocess

    from . import policy
    from .errors import relayed

    path = policy.find(args.policy)
    where = _free(Where.of(args.name or secrets.token_hex(4)))
    argv = [sys.executable, '-m', 'tooled.server', '--handle', where.handle, '--policy', str(path)]
    idle = _idle(args.idle, tied=tied)
    argv += ['--idle', repr(idle)] if idle is not None else []
    argv += ['--for', str(tied)] if tied is not None else []
    reading, writing = os.pipe()
    environ = {**os.environ, 'PYTHONPATH': os.pathsep.join(entry or str(Path.cwd()) for entry in sys.path)}
    with private(where.log) as log:
        child = subprocess.Popen(  # noqa: S603 -- tooled's own server, by the interpreter running this
            [*argv, '--ready', str(writing)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            pass_fds=(writing,),
            start_new_session=True,
            env=environ,
        )
    os.close(writing)
    line = _ready(reading, timeout=args.timeout, child=child, where=where)
    found = json.loads(line)
    if 'error' in found:
        raise relayed(found['error'], found['exit'])
    return found['ready']


def private(path: Path) -> Any:  # a text file, open for writing
    """``path`` opened to write, emptied, and the user's alone (0600): a session's log and status hold its calls'."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8')


def _ready(reading: int, *, timeout: float, child: subprocess.Popen[bytes], where: Where) -> str:
    """The line the session's process writes once it's open, or has failed to.

    Raises:
        MeshError: It ended without one, or wrote none within ``timeout`` seconds, and was killed.
    """
    import selectors

    got = b''
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as waiting, os.fdopen(reading, 'rb', buffering=0) as pipe:
        waiting.register(pipe, selectors.EVENT_READ)
        while not got.endswith(b'\n'):
            left = deadline - time.monotonic()
            if left <= 0 or not waiting.select(left):
                child.kill()
                msg = (
                    f'the session {where.handle!r} did not open within {timeout:g}s, so was ended; its log: {where.log}'
                )
                raise MeshError(msg)
            chunk = pipe.read(4096)
            if not chunk:
                code = child.wait()
                msg = (
                    f"the session {where.handle!r}'s process ended (exit {code}) before it opened; its log: {where.log}"
                )
                raise MeshError(msg)
            got += chunk
    return got.decode()


def _free(where: Where) -> Where:
    """``where``, cleared for a new session: what an ended one left is removed.

    Raises:
        UsageError: A session is open there already.
    """
    with contextlib.suppress(MeshError):
        with connected(where):
            pass
        msg = f'a session {where.handle!r} is open already: tooled session stop {where.handle} closes it'
        raise UsageError(msg)
    for leftover in (where.socket, where.status):
        leftover.unlink(missing_ok=True)
    return where


def _idle(given: float | None, *, tied: int | None) -> float | None:
    """A session's idle timeout: ``given`` (``0``: none); else none for one tied to a process, else :data:`IDLE`."""
    if given is not None:
        return given or None
    return None if tied is not None else IDLE


def _listing(*, timeout: float) -> list[dict[str, Any]]:
    """Every session under the runtime directory: an open one's status, and how each ended one did, once.

    An ended session's status file, and a socket its process left behind, are removed once listed.
    """
    base = runtime_dir()
    out = []
    for name in sorted({found.stem for found in (*base.glob('*.sock'), *base.glob('*.json'))}):
        where = Where.of(name)
        try:
            out.append(asked(where, {'control': 'status'}, until='status', timeout=timeout))
            continue
        except MeshError:
            pass
        found = where.record() or {'session': name}
        if found.get('open') is not False:
            found = {**found, 'open': False, 'reason': 'its process ended without closing it'}
        out.append(found)
        for leftover in (where.socket, where.status):
            leftover.unlink(missing_ok=True)
    return out


def _foreground(args: argparse.Namespace) -> int:
    """``session start --foreground``: open the session in this process, say where it is, and serve until it ends.

    Its exit code is 0, or 5 for a session that ended wedged.
    """
    from . import policy
    from .cli import emit
    from .server import Server

    where = _free(Where.of(args.name or secrets.token_hex(4)))
    server = Server(where, policy.find(args.policy), idle=_idle(args.idle, tied=args.tied), tied=args.tied)
    emit(server.open(), args)
    sys.stdout.flush()
    server.serve()
    return 5 if server.wedged else 0


@contextlib.contextmanager
def _forwarded(child: subprocess.Popen[bytes]) -> Generator[None]:
    """SIGINT and SIGTERM passed on to ``child`` while it runs, on the main thread; it decides what they mean."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def forward(signum: int, _frame: object) -> None:
        with contextlib.suppress(ProcessLookupError):
            child.send_signal(signum)

    held = {signum: signal.signal(signum, forward) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for signum, handler in held.items():
            signal.signal(signum, handler)
