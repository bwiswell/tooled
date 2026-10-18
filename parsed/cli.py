"""The ``parsed`` command (``python -m parsed``).

Stdlib ``argparse`` only. Every verb takes ``--policy`` (else ``$PARSED_POLICY``, else ``./parsed.toml``) after
the verb, so a permission rule can match a verb by its prefix (``parsed fetch``). ``--version`` names the seared
and zeared parsed runs on, since a protocol package parsed reads must pin the same zeared.

**Verbs:**
- ``check``: load the policy and its protocol, resolve what it injects, and summarize what it shows. With
  ``--connect``, also open the session; with ``--no-resolve``, read no inject source, so a policy can be checked
  against its protocol without its secrets (in CI, say).
- ``contracts``: every contract the policy shows, one line of each: its kind, the verbs that act on it, its topic
  with the bound slots filled, the slots left to give, and its summary.
- ``describe CONTRACT``: one contract, layered: its slots, its request's schema (a root union folded to its tags),
  and its reply as a field list. ``--reply`` gives the reply's whole schema; ``--variant TAG`` one variant's.
- ``fetch CONTRACT [slot=value …]``: a retained contract's values, those the slots select or every one.
- ``query CONTRACT [slot=value …] [--request JSON]``: ask a read queryable. ``--request`` takes JSON, ``-`` for
  stdin, or ``@path`` for a file, in the protocol's wire form, without the fields the policy injects.
  ``--param k=v`` adds a selector parameter, as a chronicler's history reads take.
- ``listen CONTRACT [slot=value …]``: each value of a retained contract or a stream as it arrives, one JSON line
  each: a retained contract's current values first, then live ones. ``--meta`` adds each one's key, origin and
  time. It stops after ``--count N``, after ``--for DURATION`` (``30``, ``30s``, ``5m``, ``1h``), or on SIGINT or
  SIGTERM, with exit 0: run under a tool that streams lines (Claude Code's Monitor), it reads like a feed.
- ``invoke CONTRACT [slot=value …] [--request JSON]``: ask a queryable that writes, its request given as
  ``query``'s is, at one key: every slot given or bound. A refusal the server sends back is a reply, and exits 0.
  A write nothing answered is asked once more, with the same request, when it carries a generated id, unless
  ``--no-retry``; still nothing, and it exits 5 with the generated values. ``--dry-run`` builds the request and
  checks it and the policy, then prints the selector and the request, injected secrets as ``"<injected>"``,
  without sending anything.

A slot left out widens to every value; a bound one is filled by the policy. ``--timeout`` bounds a query or an
invoke (5 s by default).

**Output.** Results go to stdout as JSON, indented on a terminal or with ``--pretty``. ``--select`` keeps parts of
a result; a result over the cap (``--max-bytes``, else ``$PARSED_MAX_BYTES``, else 28,000 bytes) is refused with
the size of each part; ``--out FILE`` writes it whole. A failure goes to stderr as one JSON object, ``{"error":
kind, "message": …, "path": […]}``, and the exit code says which kind (see :mod:`parsed.errors`). A note on
stderr (``{"note": …}``) says what a read left out, and a library's log line goes there as JSON too (``{"log":
level, …}``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import TYPE_CHECKING, Any

from .errors import ParsedError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .catalog import Catalog, Contract
    from .policy import Policy, Rules


#: The end of ``parsed --help``: where output goes, and what each exit code means.
EPILOG = """\
Results go to stdout as JSON. Errors go to stderr as one JSON object, notes
and log lines as JSON lines. A result over the size cap (--max-bytes, else
$PARSED_MAX_BYTES, else 28000 bytes) is refused with each part's size:
--select keeps parts, --out writes it whole.

exit codes:
  0  done (an empty result included)
  2  usage, the policy file, or the protocol
  3  refused by the policy
  4  a request that does not load: the field at fault is in "path"
  5  the mesh: the session would not open, a read or a write failed, or a
     write got no reply: "generated" holds its generated values
  6  a result over the size cap

The policy is --policy PATH, else $PARSED_POLICY, else ./parsed.toml.
"""


def build_parser() -> argparse.ArgumentParser:
    """The ``parsed`` argument parser."""
    parser = argparse.ArgumentParser(
        prog='parsed',
        description='Typed, policy-guarded access to a zeared mesh, for agents and people.',
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version', action='version', version=version())
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--policy', metavar='PATH', help='the policy file (else $PARSED_POLICY, else ./parsed.toml)')
    common.add_argument('--pretty', action='store_true', help='indent the JSON (the default on a terminal)')
    common.add_argument('--select', type=_paths, metavar='PATHS', help='keep parts: a.b, list[].key; comma-separated')
    common.add_argument(
        '--max-bytes',
        type=_bytes,
        metavar='N',
        help='refuse a result over N bytes (else $PARSED_MAX_BYTES, else 28000; 0: none)',
    )
    saving = argparse.ArgumentParser(add_help=False)
    saving.add_argument('--out', metavar='FILE', help='write the whole result to FILE, uncapped, and say where')
    verbs = parser.add_subparsers(dest='verb', metavar='verb', required=True)
    check = verbs.add_parser(
        'check',
        parents=[common, saving],
        help='check the policy and its protocol, and summarize what it shows',
        description='Load the policy and its protocol, resolve what it injects, and summarize what it shows.',
    )
    check.add_argument('--connect', action='store_true', help='also open the session the policy describes')
    check.add_argument(
        '--no-resolve',
        action='store_true',
        help='read no inject source: check the policy against its protocol without its secrets, as in CI',
    )
    check.set_defaults(run=_check)
    contracts = verbs.add_parser(
        'contracts',
        parents=[common, saving],
        help='list the contracts the policy shows',
        description='Every contract the policy shows, one line of each.',
    )
    contracts.set_defaults(run=_contracts)
    describe = verbs.add_parser(
        'describe',
        parents=[common, saving],
        help='describe one contract: its slots, and the schemas of what it takes and gives',
        description='One contract whole: its slots, and the JSON Schemas of what it takes and gives.',
    )
    describe.add_argument('contract', help='a contract: its class name, or module:Class')
    layers = describe.add_mutually_exclusive_group()
    layers.add_argument('--variant', metavar='TAG', help="one variant of the request's union, by its tag")
    layers.add_argument('--reply', action='store_true', help="the reply's whole schema, not its field list")
    describe.set_defaults(run=_describe)
    reads = argparse.ArgumentParser(add_help=False)
    reads.add_argument('contract', help='a contract: its class name, or module:Class')
    reads.add_argument('slots', nargs='*', metavar='slot=value', help='a slot to narrow by; left out, it widens')
    reads.add_argument('--timeout', type=_seconds, default=5.0, metavar='S', help='seconds to wait (default 5)')
    fetch = verbs.add_parser(
        'fetch',
        parents=[common, saving, reads],
        help="read a retained contract's values",
        description="A retained contract's values: those the slots select, or every one.",
    )
    fetch.set_defaults(run=_fetch)
    query = verbs.add_parser(
        'query',
        parents=[common, saving, reads],
        help='ask a read queryable',
        description='Ask a queryable that declares EFFECT = READ, and print its replies.',
    )
    query.add_argument('--request', metavar='JSON', help='the request, in its wire form: JSON, - for stdin, @path')
    query.add_argument('--param', action='append', default=[], metavar='k=v', help='a selector parameter')
    query.set_defaults(run=_query)
    listen = verbs.add_parser(
        'listen',
        parents=[common],
        help='stream a retained contract or a stream, one JSON line per value',
        description=(
            'Each value of a retained contract or a stream as it arrives, one JSON line each (--pretty has no '
            "effect): a retained contract's current values first, then live ones."
        ),
    )
    listen.add_argument('contract', help='a contract: its class name, or module:Class')
    listen.add_argument('slots', nargs='*', metavar='slot=value', help='a slot to narrow by; left out, it widens')
    listen.add_argument('--count', type=_count, metavar='N', help='stop after N values')
    listen.add_argument('--for', dest='duration', type=_duration, metavar='DURATION', help='stop after 30, 30s, 5m, 1h')
    listen.add_argument('--meta', action='store_true', help="add each value's key, origin and time")
    listen.set_defaults(run=_listen)
    invoke = verbs.add_parser(
        'invoke',
        parents=[common, saving],
        help='ask a queryable that writes',
        description=(
            'Ask a queryable that writes (EFFECT = WRITE or ACTION, or none declared), and print its replies. A '
            'refusal the server sends back is a reply.'
        ),
    )
    invoke.add_argument('contract', help='a contract: its class name, or module:Class')
    invoke.add_argument(
        'slots', nargs='*', metavar='slot=value', help='a slot: a write reaches one key, so each is given or bound'
    )
    invoke.add_argument('--timeout', type=_seconds, default=5.0, metavar='S', help='seconds to wait (default 5)')
    invoke.add_argument('--request', metavar='JSON', help='the request, in its wire form: JSON, - for stdin, @path')
    invoke.add_argument(
        '--dry-run',
        action='store_true',
        help='build the request and check it, and print it without sending; injected secrets show as <injected>',
    )
    invoke.add_argument(
        '--no-retry',
        action='store_true',
        help='after no reply, do not ask once more (a request with a generated id is asked twice by default)',
    )
    invoke.set_defaults(run=_invoke)
    return parser


def version() -> str:
    """``parsed X (seared Y, zeared Z)``: parsed's version, and the seared and zeared it runs on."""
    import seared
    import zeared

    from . import __version__  # lazy: the package imports this module before it sets its version

    return f'parsed {__version__} (seared {seared.__version__}, zeared {zeared.__version__})'


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``parsed`` command.

    Args:
        argv: The arguments, without the program name; ``sys.argv[1:]`` when ``None``.

    Returns:
        The exit code.
    """
    args = build_parser().parse_args(argv)
    _log_as_json()
    try:
        return args.run(args)
    except ParsedError as exc:
        sys.stderr.write(json.dumps(exc.to_json()) + '\n')
        return exc.exit_code


def emit(result: Any, args: argparse.Namespace) -> None:  # any JSON-ready value
    """Write ``result`` to stdout as JSON, after ``--select``.

    Indented when asked or on a terminal; written to ``--out``'s file instead, when given; refused when over the cap.
    """
    from .output import as_json, check_size, max_bytes, select, write_out

    if args.select:
        result = select(result, args.select)
    text = as_json(result, indent=2 if args.pretty or sys.stdout.isatty() else None)
    out = getattr(args, 'out', None)
    if out:
        text = as_json(write_out(out, text), indent=None)
    else:
        check_size(text, result, cap=max_bytes(args.max_bytes))
    sys.stdout.write(text + '\n')


def load(given: str | None) -> tuple[Path, Policy, Catalog, Rules]:
    """The policy file, the policy, its protocol's catalog, and its rules over that catalog."""
    from . import policy
    from .catalog import Catalog

    path = policy.find(given)
    loaded = policy.load(path)
    catalog = Catalog.load(loaded.protocol.packages)
    return path, loaded, catalog, policy.Rules(loaded, catalog)


def _check(args: argparse.Namespace) -> int:
    from . import policy
    from .executor import open_session

    path, loaded, catalog, rules = load(args.policy)
    shown: dict[str, list[str]] = {}
    visible = rules.visible_contracts()
    for contract in visible:
        shown.setdefault(policy.op_of(contract).value, []).append(catalog.label(contract))
    inject: dict[str, Any] = {}
    for name, source in sorted(loaded.inject.items()):
        if policy.generated(source):
            inject[name] = {'source': policy.describe(source)}  # made for each call: nothing to read, or show
        elif args.no_resolve:
            inject[name] = {'source': policy.describe(source), 'resolved': False}
        else:
            found = policy.resolve(name, source, base=path.parent)
            inject[name] = {'source': found.source, 'length': len(found.value)}
    mesh = loaded.mesh
    summary: dict[str, Any] = {
        'policy': str(path),
        'mesh': {'mode': mesh.mode.value, 'router': mesh.router, 'connect': list(mesh.connect)},
        'packages': list(loaded.protocol.packages),
        'contracts': len(catalog.contracts),
        'visible': {op.value: shown[op.value] for op in policy.Op if op.value in shown},
        'hidden': len(catalog.contracts) - len(visible),
        'bind': dict(sorted(loaded.bind.items())),
        'inject': inject,
    }
    if args.connect:
        started = time.perf_counter()
        open_session(mesh).close()
        summary['connect'] = {'ok': True, 'seconds': round(time.perf_counter() - started, 3)}
    emit(summary, args)
    return 0


def _contracts(args: argparse.Namespace) -> int:
    from .schemas import listing

    *_, rules = load(args.policy)
    emit(listing(rules), args)
    return 0


def _describe(args: argparse.Namespace) -> int:
    from .errors import RefusedError
    from .schemas import describe, describe_variant

    *_, catalog, rules = load(args.policy)
    contract = catalog.get(args.contract)
    if not rules.visible(contract):
        msg = f'the policy does not show {catalog.label(contract)}'
        raise RefusedError(msg)
    if args.variant is not None:
        emit(describe_variant(contract, args.variant, rules), args)
    else:
        emit(describe(contract, rules, reply=args.reply), args)
    return 0


def _fetch(args: argparse.Namespace) -> int:
    from . import executor

    _, loaded, catalog, rules = load(args.policy)
    contract = _readable(catalog, rules, args.contract, 'fetch')
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))
    session = executor.open_session(loaded.mesh)
    try:
        result = executor.fetch(session, contract, rules, values, timeout=args.timeout)
    finally:
        session.close()
    _finish(result, args)
    return 0


def _query(args: argparse.Namespace) -> int:
    from . import executor

    path, loaded, catalog, rules = load(args.policy)
    contract = _readable(catalog, rules, args.contract, 'query')
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))
    request = _request(args.request) if args.request is not None else None
    injected = _injected(path, loaded, contract, rules)
    session = executor.open_session(loaded.mesh)
    try:
        result = executor.query(
            session,
            contract,
            rules,
            values,
            request=request,
            injected=injected,
            params=_pairs(args.param, 'param'),
            timeout=args.timeout,
        )
    finally:
        session.close()
    _finish(result, args)
    return 0


def _invoke(args: argparse.Namespace) -> int:
    from . import executor, policy

    path, loaded, catalog, rules = load(args.policy)
    contract = _readable(catalog, rules, args.contract, 'invoke')
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))
    request = _request(args.request) if args.request is not None else None
    injected = _injected(path, loaded, contract, rules)
    generated = [name for name in injected if policy.generated(loaded.inject[name])]
    if args.dry_run:
        call = executor.prepare(contract, rules, values, request=request, injected=injected)
        secrets = [name for name in injected if name not in generated]
        emit(executor.preview(contract, call, hidden=secrets), args)
        sys.stderr.write(json.dumps({'note': 'a dry run: checked, and not sent'}) + '\n')
        return 0
    session = executor.open_session(loaded.mesh)
    try:
        result = executor.invoke(
            session,
            contract,
            rules,
            values,
            request=request,
            injected=injected,
            generated=generated,
            retry=not args.no_retry,
            timeout=args.timeout,
        )
    finally:
        session.close()
    _finish(result, args)
    return 0


def _injected(path: Path, loaded: Policy, contract: Contract, rules: Rules) -> dict[str, str]:
    """The values the policy injects into ``contract``'s request: each source read, each generated value made."""
    from . import policy, schemas

    names = schemas.injected(contract, rules)
    return {name: policy.resolve(name, loaded.inject[name], base=path.parent).value for name in names}


def _readable(catalog: Catalog, rules: Rules, name: str, verb: str) -> Contract:
    """The contract ``name`` names, if the policy shows it and ``verb`` acts on it."""
    from .errors import RefusedError, UsageError
    from .schemas import verbs

    contract = catalog.get(name)
    label = catalog.label(contract)
    if not rules.visible(contract):
        msg = f'the policy does not show {label}'
        raise RefusedError(msg)
    if verb not in verbs(contract):
        acts = ', '.join(verbs(contract)) or 'nothing in this version of parsed'
        msg = f'{verb} does not act on {label}, a {contract.kind.value} contract; what does: {acts}'
        raise UsageError(msg)
    return contract


def _pairs(items: Sequence[str], what: str) -> dict[str, str]:
    """``name=value`` arguments as a dict; each name once."""
    from .errors import UsageError

    out: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition('=')
        if not sep or not name:
            msg = f'a {what} is name=value, not {item!r}'
            raise UsageError(msg)
        if name in out:
            msg = f'{what} {name!r} is given twice'
            raise UsageError(msg)
        out[name] = value
    return out


def _request(text: str) -> dict[str, Any]:
    """``--request``: JSON, ``-`` for stdin, or ``@path`` for a file; one JSON object."""
    from pathlib import Path

    from .errors import UsageError

    source = 'the request'
    if text == '-':
        text, source = sys.stdin.read(), 'the request on stdin'
    elif text.startswith('@'):
        try:
            text, source = Path(text[1:]).expanduser().read_text(encoding='utf-8'), text[1:]
        except OSError as exc:
            msg = f'could not read the request from {text[1:]}: {exc.strerror or exc}'
            raise UsageError(msg) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        msg = f'{source} is not JSON: {exc}'
        raise UsageError(msg) from exc
    if not isinstance(data, dict):
        msg = f'{source} is a JSON object, not {type(data).__name__}'
        raise UsageError(msg)
    return data


def _seconds(text: str) -> float:
    value = float(text)
    if value <= 0:
        msg = f'a timeout is a number of seconds above zero, not {text}'
        raise argparse.ArgumentTypeError(msg)
    return value


def _finish(result: Any, args: argparse.Namespace) -> None:  # an executor.Result
    """Print a read's replies, and its notes on stderr."""
    emit(result.items, args)
    for note in result.notes:
        sys.stderr.write(json.dumps({'note': note}) + '\n')


def _listen(args: argparse.Namespace) -> int:
    import asyncio
    import signal
    import threading

    from . import executor

    _, loaded, catalog, rules = load(args.policy)
    contract = _readable(catalog, rules, args.contract, 'listen')
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))

    line = _Lines(args)

    async def run(session: Any) -> executor.Result:  # a zenoh.Session
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        # A loop takes signal handlers only on the main thread; elsewhere (parsed run inside another program) the
        # program's own handling stands, and --count or --for ends the stream.
        signals = (signal.SIGINT, signal.SIGTERM) if threading.current_thread() is threading.main_thread() else ()
        for signum in signals:
            loop.add_signal_handler(signum, stop.set)
        try:
            return await executor.listen(
                session,
                contract,
                rules,
                values,
                each=line,
                meta=args.meta,
                count=args.count,
                duration=args.duration,
                stop=stop,
            )
        finally:
            for signum in signals:
                loop.remove_signal_handler(signum)

    session = executor.open_session(loaded.mesh)
    try:
        result = asyncio.run(run(session))
    finally:
        session.close()
    for note in [*result.notes, *line.notes()]:
        sys.stderr.write(json.dumps({'note': note}) + '\n')
    return 0


class _Lines:
    """``listen``'s writer: each value, after ``--select``, as one flushed JSON line; one over the cap is left out.

    A path that matches nothing in one value doesn't end the stream; :meth:`notes` says which never matched.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        from .output import max_bytes

        self.paths, self.meta = args.select or [], args.meta
        self.cap = max_bytes(args.max_bytes)
        self.matched = [False] * len(self.paths)
        self.over = 0

    def __call__(self, value: Any) -> None:  # a JSON-ready value
        from .output import as_json, pick

        if self.paths:
            picked, found = pick(value['value'] if self.meta else value, self.paths)
            self.matched = [a or b for a, b in zip(self.matched, found, strict=True)]
            value = {**value, 'value': picked} if self.meta else picked
        text = as_json(value, indent=None)
        if self.cap and len(text.encode()) > self.cap:
            self.over += 1
            return
        sys.stdout.write(text + '\n')
        sys.stdout.flush()

    def notes(self) -> list[str]:
        """What the writer left out, and the paths that matched nothing."""
        from .output import path_text

        out = (
            [f'{self.over} values over the {self.cap:,}-byte cap were left out; --select narrows them']
            if self.over
            else []
        )
        out += [
            f'--select {path_text(path)!r} matched nothing in the stream'
            for path, found in zip(self.paths, self.matched, strict=True)
            if not found
        ]
        return out


def _paths(text: str) -> Any:  # output's parsed paths
    from .errors import UsageError
    from .output import parse_paths

    try:
        return parse_paths(text)
    except UsageError as exc:
        raise argparse.ArgumentTypeError(exc.message) from exc


def _bytes(text: str) -> int:
    value = int(text)
    if value < 0:
        msg = f'a cap is a whole number of bytes, zero or more, not {text}'
        raise argparse.ArgumentTypeError(msg)
    return value


def _count(text: str) -> int:
    value = int(text)
    if value <= 0:
        msg = f'a count is a whole number above zero, not {text}'
        raise argparse.ArgumentTypeError(msg)
    return value


_UNITS = {'s': 1.0, 'm': 60.0, 'h': 3600.0}


def _duration(text: str) -> float:
    """``30``, ``30s``, ``5m`` or ``1h``, as seconds above zero."""
    unit = text[-1:] if text[-1:] in _UNITS else 's'
    number = text[:-1] if text[-1:] in _UNITS else text
    try:
        value = float(number) * _UNITS[unit]
    except ValueError:
        value = 0.0
    if value <= 0:
        msg = f'a duration is seconds above zero, or with s, m or h: 30, 30s, 5m, 1h; not {text}'
        raise argparse.ArgumentTypeError(msg)
    return value


class _JsonLogHandler(logging.Handler):
    """A log record as one JSON line on stderr, so stderr stays one JSON object a line."""

    def emit(self, record: logging.LogRecord) -> None:
        line = {'log': record.levelname.lower(), 'logger': record.name, 'message': record.getMessage()}
        sys.stderr.write(json.dumps(line) + '\n')


def _log_as_json() -> None:
    """Send warnings and worse, from parsed's libraries, to stderr as JSON lines (once)."""
    root = logging.getLogger()
    if not any(isinstance(handler, _JsonLogHandler) for handler in root.handlers):
        root.addHandler(_JsonLogHandler(logging.WARNING))
