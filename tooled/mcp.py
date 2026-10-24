"""``tooled serve``: the tools a policy shows, over MCP on stdio, for a harness that takes tools natively.

It's a client of a session (:mod:`tooled.sessions`), as an attached call is: the one ``--session`` names, else
``$TOOLED_SESSION``, so an agent's command-line calls and its MCP tools share one session, one state and one mesh; or,
with none named, one of its own, opened from the policy and tied to this process. The session does the work: a list
is its ``tooled tools --format mcp``, and a call its ``tooled call NAME --args JSON``.

**The protocol** is MCP's stdio binding: JSON-RPC 2.0, one message a line on stdin and stdout, written on the
standard library. It speaks both eras of the specification:
- **legacy** (2025-11-25 and before): a client opens with ``initialize``, and is answered in the version it asked
  for, or the newest legacy one; ``notifications/initialized`` and ``ping`` are taken;
- **modern** (2026-07-28): each request carries its version in ``_meta`` (``io.modelcontextprotocol/protocolVersion``),
  a version ``serve`` doesn't speak is refused (``-32022``, with the versions it does), and ``server/discover`` says
  what it speaks, can do and is.

Every result says ``resultType: "complete"`` and names the server in its ``_meta``; a list says how long it keeps
(``ttlMs``, ``cacheScope``).

**Tools:** ``tools/list`` gives every tool the policy shows, or those ``--verbs`` picks, in one page, in the
export's order. ``tools/call`` carries one out: exit 0 is a result; any other exit is a result with ``isError``, its
text tooled's error line (worded for the model, so it can correct the call) and any notes. A name the policy shows
no tool of is a protocol error (``-32602``). Calls run side by side, each its own call in the session, and
``notifications/cancelled`` stops one, which then has no response. Each call appends its line to ``$TOOLED_LOG``, as
an attached call does.

**Shutdown:** the end of stdin stops the calls still running, and closes the session ``serve`` opened for itself.

Nothing but MCP's messages goes to stdout; notes go to stderr, as JSON lines.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import MeshError, TooledError, UsageError
from .sessions import SESSION_ENV, Exchange, Where, asked, own_variables

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping, Sequence

#: The protocol versions spoken by per-request metadata (modern), and by an ``initialize`` handshake (legacy).
MODERN = ('2026-07-28',)
LEGACY = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
SUPPORTED = (*MODERN, *LEGACY)

#: How long a client may keep the tools listed, in milliseconds: the session's policy is fixed while it's open.
TTL_MS = 3_600_000

#: JSON-RPC's error codes, and MCP's for a version not spoken.
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
UNSUPPORTED_VERSION = -32022

#: What the server tells a model of itself.
INSTRUCTIONS = (
    "Typed tools a policy allows. Each call is checked against the policy and the tool's own types before it runs; "
    'a refusal comes back as an error saying why and what to give instead, and a result is JSON.'
)

#: How long the calls still running are given to end, once stdin has, in seconds.
ENDING = 5.0

_VERSION = 'io.modelcontextprotocol/protocolVersion'
_SERVER_INFO = 'io.modelcontextprotocol/serverInfo'


class Served:
    """The MCP side of ``tooled serve``: messages read, and answered from the session at ``where``.

    Args:
        where: The session its calls run in.
        verbs: The ``--verbs`` picks for its list, or ``None`` for every tool.
        environ: The ``TOOLED_*`` variables each call brings (its cap among them).
        cwd: The working directory each call brings.
    """

    def __init__(self, where: Where, *, verbs: str | None, environ: Mapping[str, str], cwd: str) -> None:
        from . import __version__

        self.where, self.verbs, self.environ, self.cwd = where, verbs, dict(environ), cwd
        self.info = {'name': 'tooled', 'version': __version__}
        self.names: set[str] = set()
        self._notes: set[str] = set()  # each note the session's export gave, passed on once
        self._out: Any = None  # stdout's bytes
        self._lock = threading.Lock()
        self._calls: dict[Any, Exchange] = {}
        self._cancelled: set[Any] = set()
        self._workers: list[threading.Thread] = []

    def open(self) -> None:
        """Read the tools, picked and every one, before the first message: an export that fails, fails here.

        Raises:
            TooledError: The export failed, as ``tooled tools`` would.
        """
        self.listed()
        self.names = {tool['name'] for tool in self._exported([])} if self.verbs else self.names

    def listed(self) -> list[dict[str, Any]]:
        """The tools, as the session exports them in MCP's form, picked.

        Raises:
            TooledError: The export failed, as ``tooled tools`` would.
        """
        tools = self._exported(['--verbs', self.verbs] if self.verbs else [])
        if not self.verbs:
            self.names = {tool['name'] for tool in tools}
        return tools

    def known(self, name: str) -> bool:
        """Whether the policy shows a tool of ``name``, picked or not; the names read again when it's not among them.

        Raises:
            TooledError: The export failed, as ``tooled tools`` would.
        """
        if name not in self.names:
            self.names = {tool['name'] for tool in self._exported([])}
        return name in self.names

    def _exported(self, picked: Sequence[str]) -> list[dict[str, Any]]:
        out, err = [], []
        argv = ['tools', '--format', 'mcp', '--max-bytes', '0', *picked]
        with Exchange(self.where, argv, environ=self.environ, cwd=self.cwd, tty=False, stdin=None) as call:
            ended = call.ended(out=out.append, err=err.append)
        if ended.code != 0:
            from .errors import relayed

            found = json.loads(''.join(err).splitlines()[-1]) if err else {'error': 'error', 'message': 'no export'}
            raise relayed(found, ended.code)
        for note in ''.join(err).splitlines():
            if note.strip() and note not in self._notes:
                self._notes.add(note)
                _noted(note)
        return json.loads(''.join(out))

    def run(self, reading: Any, writing: Any) -> None:  # binary streams: stdin's, stdout's
        """Answer each message on ``reading`` until it ends, on ``writing``; then stop the calls still running."""
        self._out = writing
        try:
            for raw in reading:
                text = raw.decode('utf-8', errors='replace').strip()
                if text:
                    self._read(text)
        finally:
            with self._lock:
                running = list(self._calls.values())
            for call in running:
                call.stop(signal.SIGTERM)
            for worker in list(self._workers):
                worker.join(ENDING)

    def _read(self, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            self._error(None, PARSE_ERROR, 'Parse error: not JSON')
            return
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            ident = message.get('id') if isinstance(message, dict) else None
            self._error(ident, INVALID_REQUEST, 'Invalid request: one JSON-RPC 2.0 message a line, not a batch')
            return
        method = message.get('method')
        if not isinstance(method, str):
            return  # a response, or nothing: serve sends no requests to answer
        params = message.get('params') or {}
        if 'id' not in message:
            self._noticed(method, params)
            return
        ident = message['id']
        if not isinstance(params, dict):
            self._error(ident, INVALID_PARAMS, 'Invalid params: an object')
            return
        version = (params.get('_meta') or {}).get(_VERSION)
        if version is not None and version not in SUPPORTED:
            data = {'supported': list(SUPPORTED), 'requested': version}
            self._error(ident, UNSUPPORTED_VERSION, 'Unsupported protocol version', data=data)
            return
        self._answered(ident, method, params)

    def _answered(self, ident: Any, method: str, params: dict[str, Any]) -> None:
        if method == 'initialize':
            asked_for = params.get('protocolVersion')
            self._result(
                ident,
                {
                    'protocolVersion': asked_for if asked_for in LEGACY else LEGACY[0],
                    'capabilities': {'tools': {}},
                    'serverInfo': self.info,
                    'instructions': INSTRUCTIONS,
                },
            )
        elif method == 'server/discover':
            self._result(
                ident,
                {
                    'supportedVersions': list(SUPPORTED),
                    'capabilities': {'tools': {}},
                    'instructions': INSTRUCTIONS,
                    'ttlMs': TTL_MS,
                    'cacheScope': 'private',
                },
            )
        elif method == 'ping':
            self._result(ident, {})
        elif method == 'tools/list':
            self._worker(self._list, ident)
        elif method == 'tools/call':
            name, arguments = params.get('name'), params.get('arguments', {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                self._error(ident, INVALID_PARAMS, "Invalid params: 'name', a string, and 'arguments', an object")
                return
            self._worker(self._call, ident, name, arguments)
        else:
            self._error(ident, METHOD_NOT_FOUND, f'Method not found: {method}')

    def _noticed(self, method: str, params: Any) -> None:  # the notification's params
        """A notification: a cancellation stops its call; any other is taken, with nothing to do."""
        if method != 'notifications/cancelled' or not isinstance(params, dict):
            return
        ident = params.get('requestId')
        with self._lock:
            self._cancelled.add(ident)
            call = self._calls.get(ident)
        if call is not None:
            call.stop(signal.SIGINT)

    def _worker(self, work: Any, *given: Any) -> None:
        worker = threading.Thread(target=work, args=given, name='tooled serve', daemon=True)
        with self._lock:
            self._workers = [each for each in self._workers if each.is_alive()]
            self._workers.append(worker)
        worker.start()

    def _list(self, ident: Any) -> None:
        try:
            tools = self.listed()
        except TooledError as exc:
            self._error(ident, INTERNAL_ERROR, f'the tools could not be listed: {exc.message}', data=exc.to_json())
            return
        self._result(ident, {'tools': tools, 'ttlMs': TTL_MS, 'cacheScope': 'private'})

    def _call(self, ident: Any, name: str, arguments: dict[str, Any]) -> None:
        try:
            known = self.known(name)
        except TooledError as exc:
            self._error(ident, INTERNAL_ERROR, f'the tools could not be listed: {exc.message}', data=exc.to_json())
            return
        if not known:
            self._error(ident, INVALID_PARAMS, f'Unknown tool: {name}')
            return
        code, out, err = self._carried(ident, name, arguments)
        with self._lock:
            cancelled = ident in self._cancelled
            self._cancelled.discard(ident)
        if cancelled:
            return  # a cancelled request has no response
        self._result(ident, {'content': _content(out, err), 'isError': code != 0})

    def _carried(self, ident: Any, name: str, arguments: dict[str, Any]) -> tuple[int, str, str]:
        """``name`` called in the session with ``arguments``: its exit code, stdout and stderr; its line logged."""
        from .cli import VERBS, _log_call
        from .ledger import Entry

        argv = ['call', name, '--args', json.dumps(arguments)]
        entry = Entry.begin(argv, VERBS)
        entry.session = self.where.handle
        out: list[str] = []
        err: list[str] = []
        try:
            with Exchange(self.where, argv, environ=self.environ, cwd=self.cwd, tty=False, stdin=None) as call:
                with self._lock:
                    self._calls[ident] = call
                    cancelled = ident in self._cancelled
                if cancelled:
                    call.stop(signal.SIGINT)
                ended = call.ended(out=out.append, err=err.append)
        except TooledError as exc:
            err.append(json.dumps(exc.to_json()) + '\n')
            entry.end(exc.exit_code, exc)
            _log_call(entry)
            return exc.exit_code, ''.join(out), ''.join(err)
        finally:
            with self._lock:
                self._calls.pop(ident, None)
        if ended.line is None:
            err.append(json.dumps({'error': 'error', 'message': f'the call was cut short (exit {ended.code})'}) + '\n')
            return ended.code or 1, ''.join(out), ''.join(err)
        entry.took(ended.line)
        entry.end(ended.code)
        _log_call(entry)
        return ended.code, ''.join(out), ''.join(err)

    def _result(self, ident: Any, result: dict[str, Any]) -> None:
        self._send({'jsonrpc': '2.0', 'id': ident, 'result': _completed(result, self.info)})

    def _error(self, ident: Any, code: int, message: str, *, data: Any = None) -> None:
        error: dict[str, Any] = {'code': code, 'message': message}
        if data is not None:
            error['data'] = data
        self._send({'jsonrpc': '2.0', 'id': ident, 'error': error})

    def _send(self, message: dict[str, Any]) -> None:
        line = (json.dumps(message) + '\n').encode()  # ASCII: no character a reader could take for a line's end
        with self._lock:
            self._out.write(line)
            self._out.flush()


def _completed(result: dict[str, Any], info: Mapping[str, str]) -> dict[str, Any]:
    """A result as every one is sent: complete, and naming the server."""
    meta = {**result.get('_meta', {}), _SERVER_INFO: dict(info)}
    return {'resultType': 'complete', **result, '_meta': meta}


def _content(out: str, err: str) -> list[dict[str, str]]:
    """A call's content: what it wrote to stdout, then what it wrote to stderr (its error, its notes)."""
    items = [{'type': 'text', 'text': text.rstrip('\n')} for text in (out, err) if text.strip()]
    return items or [{'type': 'text', 'text': ''}]


def _noted(text: str) -> None:
    """Pass a session's notes on to this process's stderr, where a harness may read them."""
    if text:
        sys.stderr.write(text if text.endswith('\n') else text + '\n')
        sys.stderr.flush()


def serve(args: argparse.Namespace) -> int:
    """``tooled serve``: MCP on stdio, attached to the session named, or to one of its own; until stdin ends.

    Raises:
        UsageError: The session named holds another policy than the one named.
        MeshError: The session named isn't open, or the one of its own wouldn't open.
        TooledError: The tools can't be exported, as ``tooled tools`` says.
    """
    from . import sessions
    from .policy.config import POLICY_ENV

    handle = args.session if args.session is not None else os.environ.get(SESSION_ENV, '')
    owned: Where | None = None
    if handle:
        where = Where.of(handle)
        held = asked(where, {'control': 'status'}, until='status', timeout=args.timeout)
        named = args.policy or os.environ.get(POLICY_ENV)
        if named and Path(named).expanduser().resolve() != Path(held['policy']):
            msg = f'the session {where.handle!r} holds the policy {held["policy"]}, and serve names {named}'
            raise UsageError(msg)
    else:
        where = owned = Where.of(sessions.started(args, tied=os.getpid())['session'])
    environ = {name: value for name, value in own_variables().items() if name not in {POLICY_ENV, SESSION_ENV}}
    served = Served(where, verbs=','.join(args.verbs) if args.verbs else None, environ=environ, cwd=str(Path.cwd()))
    try:
        served.open()
        with _ended_by_signals():
            served.run(sys.stdin.buffer, sys.stdout.buffer)
    finally:
        if owned is not None:
            with contextlib.suppress(MeshError):
                sessions.stopped(owned, timeout=args.timeout)
    return 0


@contextlib.contextmanager
def _ended_by_signals() -> Any:  # a context manager
    """SIGTERM ends ``serve`` as the end of stdin does, on the main thread, so the session it opened is closed."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def ended(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    held = signal.signal(signal.SIGTERM, ended)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, held)
