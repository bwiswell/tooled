"""A session's process: a policy's world held open, and the calls that attach to it, run in it side by side.

``tooled session start`` runs it as ``python -m tooled.server`` in a process of its own (:mod:`tooled.sessions`), or
in its own with ``--foreground``.

**What it holds** (:class:`Held`), from its start: the policy as it was loaded, its catalog and rules, each value it
injects (resolved once, in the environment of what started it; a generated one is still made for each write), the
mesh the policy names, open, and the app, its session open on the state the policy builds
(:class:`tooled.tree.Held`). A call naming another policy is refused.

**How a call runs:** in a thread of its own, with its own streams, directory and variables (:mod:`tooled.attached`),
through the same command a cold call runs, so its output, its errors and its exit code are a cold call's. A wire
verb's runs on the mesh the session holds, side by side with the rest; a listen or a wait ends when its client asks,
as a signal ends a cold one. A line runs on the app's loop, guarded as a cold one is: a handler's output kept
apart, its stdin empty, and a line past its bound cancelled (exit 7), the session going on. A handler that blocks
the loop can't be cancelled: the session is wedged, every call on it fails (exit 5), and it ends, its state not
left.

**How it ends:** asked to (``session stop``, SIGTERM, SIGINT); when the process it's for ends (``--for``); idle for
its timeout, with no call in flight; or wedged. Its calls in flight are given a few seconds to end, then stopped as
their clients would; the app's session is closed, so the state's own goodbye runs, then the mesh; and its status
file says how it ended, its socket removed.

**Who attaches:** its socket is made the user's alone (0600), in a directory that is; and where the system tells a
peer's user, a peer that isn't this one's is refused.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import selectors
import signal
import socket
import struct
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import attached
from .errors import RefusedError, TooledError, UsageError
from .sessions import Where, failed, send

if TYPE_CHECKING:
    from collections.abc import Mapping

    from parsed import App

    from .catalog import Catalog
    from .policy import Policy, Rules
    from .tree import Held as Lines

#: How long a closing session gives its calls in flight to end on their own, in seconds, before it stops them.
ENDING = 5.0

#: How long the app's session is given to open, or to close, in seconds.
OPENING = 30.0

#: How often the session looks at its idle time, and at the process it's for, in seconds.
_TICK = 1.0


@dataclass
class Held:
    """What a session holds for its calls.

    Attributes:
        where: Where it lives.
        path: The policy file, as it was found when the session started.
        policy: The policy, as loaded then.
        catalog: Its catalog.
        rules: Its rules.
        injected: Each value it injects that isn't generated, as read then.
        mesh: The mesh it names, open, for one with ``[mesh]``: a zenoh session, or zeared's managed one.
        app: Its app, for one with ``[app]``.
        lines: The app's session, open on its state, for its lines.
    """

    where: Where
    path: Path
    policy: Policy
    catalog: Catalog
    rules: Rules
    injected: dict[str, str]
    mesh: Any = None
    app: App | None = None
    lines: Lines | None = None

    def loaded(self, given: str | None) -> tuple[Path, Policy, Catalog, Rules]:
        """What a call's ``load`` gives: the session's own policy, once the call's is held to it.

        Raises:
            UsageError: The call names a policy (``--policy``, or ``$TOOLED_POLICY`` in its environment) that isn't
                the session's.
        """
        from .policy.config import POLICY_ENV

        call_env = attached.environ()
        named, via = (given, '--policy') if given else (call_env.get(POLICY_ENV), f'${POLICY_ENV}')
        if named and attached.path(named).resolve() != self.path:
            msg = (
                f'the session {self.where.handle!r} holds the policy {self.path}, and this call names '
                f'{attached.path(named)} ({via}): a session runs only its own'
            )
            raise UsageError(msg)
        return self.path, self.policy, self.catalog, self.rules


class Server:
    """A session: what it holds, its socket, and the calls attached to it.

    Args:
        where: Where it lives.
        path: Its policy file.
        idle: Its idle timeout, in seconds; ``None`` for none.
        tied: The process it's for, which it ends with; ``None`` for none.
    """

    def __init__(self, where: Where, path: Path, *, idle: float | None, tied: int | None) -> None:
        self.where, self.path, self.idle, self.tied = where, path, idle, tied
        self.held: Held | None = None
        self.wedged = False
        self.reason: str | None = None
        self.record: dict[str, Any] = {}
        self._listener: socket.socket | None = None
        self._wake = socket.socketpair()
        self._wake[0].settimeout(0)
        self._calls: set[attached.Call] = set()
        self._lock = threading.Condition()
        self._done = threading.Event()
        self._stopping: list[socket.socket] = []  # the links that asked it to stop, told once it has
        self._started = ''
        self._last = time.monotonic()
        self._last_at: str | None = None
        self._served = 0

    def open(self) -> dict[str, Any]:
        """Load the policy, open what it names, and listen: what a ``session start`` says of it.

        Raises:
            TooledError: The policy's, its catalog's, or its app's, as a cold call's would be.
        """
        from .cli import _log_as_json

        _log_as_json()  # once, before calls run side by side
        began = time.perf_counter()
        try:
            self.held = self._held()
            self._listen()
        except BaseException:
            if self.held is not None:
                _let_go(self.held)
            for end in self._wake:
                end.close()
            raise
        self._started = _now()
        self._write(self.status())
        return {**self.status(), 'seconds': round(time.perf_counter() - began, 3)}

    def _held(self) -> Held:
        """What the session holds: the policy loaded, each source read, the mesh opened, and the app's session."""
        from . import executor, policy, tree
        from .cli import load

        path, loaded, catalog, rules = load(str(self.path))
        injected = {
            name: policy.resolve(name, source, base=path.parent).value
            for name, source in sorted(loaded.inject.items())
            if not policy.generated(source)
        }
        held = Held(self.where, path, loaded, catalog, rules, injected)
        try:
            if loaded.mesh is not None:
                held.mesh = executor.open_session(loaded.mesh)
            if loaded.app is not None:
                held.app = tree.app_of(loaded)
                held.lines = tree.Held(held.app, tree.state_of(loaded, path), on_wedged=self._wedged)
                held.lines.open(timeout=OPENING)
        except BaseException:
            _let_go(held)
            raise
        return held

    def status(self) -> dict[str, Any]:
        """What ``session status`` says of it, open."""
        with self._lock:
            calls, in_flight = self._served, len(self._calls)
        return {
            'session': self.where.handle,
            'open': True,
            'pid': os.getpid(),
            'socket': str(self.where.socket),
            'policy': str(self.path),
            'mesh': self.held is not None and self.held.mesh is not None,
            'app': self.held is not None and self.held.lines is not None,
            'started': self._started,
            'last_call': self._last_at,
            'calls': calls,
            'in_flight': in_flight,
            'idle': self.idle,
            'for': self.tied,
            'log': str(self.where.log),
        }

    def serve(self) -> str:
        """Serve calls until the session ends: why it did.

        On the main thread, SIGTERM, SIGINT and SIGHUP end it; its standard streams are routed by call while it
        serves.
        """
        restore = attached.install()
        signals = self._signals()
        try:
            with selectors.DefaultSelector() as waiting:
                waiting.register(self._listener, selectors.EVENT_READ, 'call')  # ty: ignore[invalid-argument-type]
                waiting.register(self._wake[0], selectors.EVENT_READ, 'wake')
                tied = self._watched(waiting)
                while self.reason is None:
                    for key, _ in waiting.select(_TICK):
                        if key.data == 'call':
                            self._accept()
                        elif key.data == 'tied':
                            self.end(f'the process it was for ({self.tied}) ended')
                    self._looked(tied)
            return self._close()
        finally:
            for signum, handler in signals.items():
                signal.signal(signum, handler)
            restore()

    def end(self, reason: str) -> None:
        """Ask the session to end, for ``reason``, from any thread; the first reason given stands."""
        with self._lock:
            if self.reason is None:
                self.reason = reason
        with contextlib.suppress(OSError):
            self._wake[1].send(b'.')

    def _wedged(self, reason: str) -> None:
        self.wedged = True
        self.end(reason)

    def _listen(self) -> None:
        """Listen on the socket, made the user's alone."""
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        held = os.umask(0o177)
        try:
            listener.bind(str(self.where.socket))
        except OSError as exc:
            listener.close()
            msg = f'could not listen at {self.where.socket}: {exc.strerror or exc}'
            raise UsageError(msg) from exc
        finally:
            os.umask(held)
        listener.listen()
        self._listener = listener

    def _signals(self) -> dict[int, Any]:
        """The signals that end the session, on the main thread, and the handlers they had."""
        if threading.current_thread() is not threading.main_thread():
            return {}

        def ended(signum: int, _frame: object) -> None:
            self.end(signal.Signals(signum).name)

        return {signum: signal.signal(signum, ended) for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}

    def _watched(self, waiting: selectors.BaseSelector) -> bool:
        """Watch the process the session is for, by a descriptor where the system gives one: whether it's polled."""
        if self.tied is None:
            return False
        try:
            found = os.pidfd_open(self.tied)
        except AttributeError, OSError:
            return True
        waiting.register(found, selectors.EVENT_READ, 'tied')
        return False

    def _looked(self, polled: bool) -> None:  # noqa: FBT001 -- whether the tied process is polled
        """End the session when it's been idle long enough, or its process has ended (when it's polled)."""
        with contextlib.suppress(BlockingIOError):
            self._wake[0].recv(64)
        if polled and self.tied is not None:
            try:
                os.kill(self.tied, 0)
            except ProcessLookupError:
                self.end(f'the process it was for ({self.tied}) ended')
            except PermissionError:
                pass  # there, and another user's
        with self._lock:
            idle = self.idle is not None and not self._calls and time.monotonic() - self._last > self.idle
        if idle:
            self.end(f'idle for {self.idle:g}s')

    def _accept(self) -> None:
        assert self._listener is not None  # noqa: S101 -- serve() runs after open()
        try:
            link, _ = self._listener.accept()
        except OSError:
            return
        threading.Thread(target=self._connection, args=(link,), name='tooled call', daemon=True).start()

    def _connection(self, link: socket.socket) -> None:
        """One connection: a call, or a question of the session's."""
        with link, contextlib.suppress(OSError, ValueError):  # a client gone, or not tooled's
            reader = link.makefile('r', encoding='utf-8', newline='\n')
            if not _peer_is_us(link):
                error = RefusedError(f"the session {self.where.handle!r} is another user's")
                send(link, {'err': json.dumps(error.to_json()) + '\n'})
                send(link, {'end': error.exit_code, 'cut': True})
                return
            request = json.loads(reader.readline() or 'null')
            if not isinstance(request, dict):
                return
            if 'control' in request:
                self._control(link, request)
            elif self.reason is None:
                stops = self._call(link, reader, request)
                with contextlib.suppress(OSError):  # its end is sent: the client reads it, then the link's end
                    link.shutdown(socket.SHUT_RDWR)
                stops.join(ENDING)
            reader.close()

    def _control(self, link: socket.socket, request: Mapping[str, Any]) -> None:
        if request['control'] == 'status':
            send(link, {'status': self.status()})
        elif request['control'] == 'stop':
            send(link, {'stopping': self.status()})
            with self._lock:
                self._stopping.append(link)
            self.end('stopped')
            self._done.wait(ENDING * 2 + OPENING * 2)

    def _call(self, link: socket.socket, reader: Any, request: Mapping[str, Any]) -> threading.Thread:  # reader: lines
        """Run one call, its output sent back as it's written, and its end: the thread reading its client's stops."""
        frames = _Frames(link)
        call = attached.Call(
            out=frames.stream('out'),
            err=frames.stream('err'),
            stdin=io.StringIO(request.get('stdin') or ''),
            cwd=str(request.get('cwd') or '/'),
            environ={str(k): str(v) for k, v in (request.get('env') or {}).items()},
            tty=bool(request.get('tty')),
            held=self.held,
        )
        frames.gone = call.stop.by
        with self._lock:
            self._calls.add(call)
        stops = threading.Thread(target=_stops, args=(reader, call), name='tooled stop', daemon=True)
        stops.start()
        token = attached.CALL.set(call)
        try:
            frames.send(self._ran(request))
        finally:
            attached.CALL.reset(token)
            frames.release()
            with self._lock:
                self._calls.discard(call)
                self._served += 1
                self._last, self._last_at = time.monotonic(), _now()
                self._lock.notify_all()
        return stops

    def _ran(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The call run as a cold one runs, its errors written as a cold one's are: its end frame."""
        from . import __version__, cli
        from .ledger import Entry

        argv = [str(word) for word in request.get('argv') or []]
        entry = Entry.begin(argv, cli.VERBS)
        try:
            if request.get('tooled') != __version__:
                msg = (
                    f'the session {self.where.handle!r} runs tooled {__version__}, and this call is tooled '
                    f'{request.get("tooled")}: start the session again with this tooled'
                )
                code = failed(UsageError(msg), entry)
            else:
                code = cli.ran(argv, entry)
        except SystemExit as exc:  # --help, --version: cut short, as a cold run is
            return {'end': _exit_code(exc.code), 'cut': True}
        except attached.Stopped as exc:
            return {'end': 128 + exc.signum, 'cut': True}
        except Exception:  # noqa: BLE001 -- a crash, written as Python writes one, and the call cut short
            sys.stderr.write(traceback.format_exc())
            return {'end': 1, 'cut': True}
        return {'end': code, 'entry': entry.as_json()}

    def _close(self) -> str:
        """Close the session: its calls ended, its state left (unless wedged), its socket removed, its end written."""
        if self._listener is not None:
            self._listener.close()
        self.where.socket.unlink(missing_ok=True)
        if not self.wedged:
            self._waited(ENDING)
        with self._lock:
            for call in self._calls:
                call.stop.by(signal.SIGTERM)
        self._waited(ENDING)
        reason = self.reason or 'stopped'
        lines = self.held.lines if self.held is not None else None
        if lines is not None and not self.wedged:
            try:
                lines.close(timeout=OPENING)
            except Exception as exc:  # noqa: BLE001 -- the session ends regardless, saying so
                reason += f"; leaving the app's state failed: {type(exc).__name__}: {exc}"
        if self.held is not None and self.held.mesh is not None:
            with contextlib.suppress(Exception):  # closing regardless: zenoh's own error type
                self.held.mesh.close()
        self.reason = reason
        with self._lock:
            calls = self._served
        self.record = {
            'session': self.where.handle,
            'open': False,
            'pid': os.getpid(),
            'policy': str(self.path),
            'started': self._started,
            'ended': _now(),
            'reason': reason,
            'calls': calls,
        }
        self._write(self.record)
        with self._lock:
            asked, self._stopping = self._stopping, []
        for link in asked:
            with contextlib.suppress(OSError):
                send(link, {'stopped': self.record})
        for end in self._wake:
            end.close()
        self._done.set()
        return reason

    def _waited(self, within: float) -> None:
        """Wait up to ``within`` seconds for the calls in flight to end."""
        deadline = time.monotonic() + within
        with self._lock:
            while self._calls and (left := deadline - time.monotonic()) > 0:
                self._lock.wait(left)

    def _write(self, record: Mapping[str, Any]) -> None:
        """Write the status file whole, or not at all."""
        from .sessions import private

        part = self.where.status.with_name(self.where.status.name + '.part')
        with contextlib.suppress(OSError):  # the session runs without one, and status asks it
            with private(part) as out:
                out.write(json.dumps(record, ensure_ascii=False) + '\n')
            part.replace(self.where.status)


def _let_go(held: Held) -> None:
    """Close what ``held`` opened, as far as it got: a session that won't open leaves nothing open."""
    if held.lines is not None:
        with contextlib.suppress(Exception):
            held.lines.close(timeout=OPENING)
    if held.mesh is not None:
        with contextlib.suppress(Exception):
            held.mesh.close()


class _Frames:
    """A call's link back to its client: each frame one JSON line, whole, whichever thread writes it."""

    def __init__(self, link: socket.socket) -> None:
        self.link: socket.socket | None = link
        self.lock = threading.Lock()
        self.lost = False
        self.gone: Any = lambda _signum: None  # told once the client is gone

    def send(self, frame: Mapping[str, Any]) -> None:
        """Send ``frame``; once the client is gone, or the call has ended, nothing."""
        with self.lock:
            if self.lost or self.link is None:
                return
            try:
                send(self.link, frame)
            except OSError:
                self.lost = True
        if self.lost:
            self.gone(signal.SIGPIPE)

    def release(self) -> None:
        """Let go of the link once the call has ended, so what still holds the call (a task's context) holds none."""
        with self.lock:
            self.link = None

    def stream(self, kind: str) -> _FrameStream:
        """A text stream whose writes are sent as ``kind``'s frames."""
        return _FrameStream(self, kind)


class _FrameStream(io.TextIOBase):
    def __init__(self, frames: _Frames, kind: str) -> None:
        self.frames, self.kind = frames, kind

    def write(self, text: str) -> int:
        """Send ``text`` to the client, at once."""
        if text:
            self.frames.send({self.kind: text})
        return len(text)


def _stops(reader: Any, call: attached.Call) -> None:  # reader: the link's lines
    """Read what else the client sends while its call runs: a stop, by a signal's number; or its going."""
    with contextlib.suppress(OSError, ValueError):
        for line in reader:
            frame = json.loads(line)
            if isinstance(frame, dict) and 'stop' in frame:
                call.stop.by(int(frame['stop']))
    call.stop.by(signal.SIGPIPE)  # gone: whatever the call does now, no one reads it


def _peer_is_us(link: socket.socket) -> bool:
    """Whether the peer runs as this process's user, where the system says; where it doesn't, the socket's mode."""
    option = getattr(socket, 'SO_PEERCRED', None)
    if option is None:
        return True
    _pid, uid, _gid = struct.unpack('3i', link.getsockopt(socket.SOL_SOCKET, option, struct.calcsize('3i')))
    return uid == os.getuid()


def _exit_code(code: object) -> int:
    """``SystemExit``'s code as a process's: ``None`` 0, a number itself, anything else 1."""
    if code is None:
        return 0
    return code if isinstance(code, int) else 1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def main(argv: list[str] | None = None) -> int:
    """``python -m tooled.server``: open the session, say so on ``--ready``'s descriptor, and serve until it ends."""
    parser = argparse.ArgumentParser(prog='python -m tooled.server', description='A tooled session (internal).')
    parser.add_argument('--handle', required=True)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--idle', type=float)
    parser.add_argument('--for', dest='tied', type=int)
    parser.add_argument('--ready', type=int, required=True)
    args = parser.parse_args(argv)
    with os.fdopen(args.ready, 'w', encoding='utf-8') as ready:
        try:
            server = Server(Where.of(args.handle), Path(args.policy), idle=args.idle, tied=args.tied)
            summary = server.open()
        except TooledError as exc:
            ready.write(json.dumps({'error': exc.to_json(), 'exit': exc.exit_code}) + '\n')
            return exc.exit_code
        ready.write(json.dumps({'ready': summary}) + '\n')
    server.serve()
    sys.stdout.flush()
    sys.stderr.flush()
    if server.wedged:  # a thread is stuck in the app's handler: the process ends without it
        os._exit(5)
    return 0


if __name__ == '__main__':
    sys.exit(main())
