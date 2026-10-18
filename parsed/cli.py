"""The ``parsed`` command (``python -m parsed``).

Stdlib ``argparse`` only. Every verb takes ``--policy`` (else ``$PARSED_POLICY``, else ``./parsed.toml``) after
the verb, so a permission rule can match a verb by its prefix (``parsed check``).

Results go to stdout as JSON, indented on a terminal or with ``--pretty``. A failure goes to stderr as one JSON
object, ``{"error": kind, "message": …, "path": […]}``, and the exit code says which kind (see
:mod:`parsed.errors`). ``--version`` names the seared and zeared parsed runs on, since a protocol package parsed
reads must pin the same zeared.

**Verbs:**
- ``check``: load the policy and its protocol, resolve what it injects, and summarize what it shows. With
  ``--connect``, also open the session.
- ``contracts``: every contract the policy shows, one line of each: its kind, the verbs that act on it, its topic
  with the bound slots filled, the slots left to give, and its summary.
- ``describe CONTRACT``: one contract whole: its slots, and the JSON Schemas of what it takes and gives.
  ``--variant TAG`` gives one variant of its request's union instead.
- ``fetch CONTRACT [slot=value …]``: a retained contract's values, those the slots select or every one.
- ``query CONTRACT [slot=value …] [--request JSON]``: ask a read queryable. ``--request`` takes JSON, ``-`` for
  stdin, or ``@path`` for a file, in the protocol's wire form, without the fields the policy injects.
  ``--param k=v`` adds a selector parameter, as a chronicler's history reads take.
- ``listen CONTRACT [slot=value …]``: each value of a retained contract or a stream as it arrives, one JSON line
  each: a retained contract's current values first, then live ones. ``--meta`` adds each one's key, origin and
  time. It stops after ``--count N``, after ``--for DURATION`` (``30``, ``30s``, ``5m``, ``1h``), or on SIGINT or
  SIGTERM, with exit 0: run under a tool that streams lines (Claude Code's Monitor), it reads like a feed.

A slot left out widens to every value; a bound one is filled by the policy. ``--timeout`` bounds a query (5 s by
default). A note on stderr (``{"note": …}``) says what was left out: a reply that wouldn't decode, no reply, a
sample on a denied key. A library's log line goes to stderr as JSON too (``{"log": level, …}``).
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


def build_parser() -> argparse.ArgumentParser:
    """The ``parsed`` argument parser."""
    parser = argparse.ArgumentParser(
        prog='parsed',
        description='Typed, policy-guarded access to a zeared mesh, for agents and people.',
    )
    parser.add_argument('--version', action='version', version=version())
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--policy', metavar='PATH', help='the policy file (else $PARSED_POLICY, else ./parsed.toml)')
    common.add_argument('--pretty', action='store_true', help='indent the JSON (the default on a terminal)')
    verbs = parser.add_subparsers(dest='verb', metavar='verb', required=True)
    check = verbs.add_parser(
        'check',
        parents=[common],
        help='check the policy and its protocol, and summarize what it shows',
        description='Load the policy and its protocol, resolve what it injects, and summarize what it shows.',
    )
    check.add_argument('--connect', action='store_true', help='also open the session the policy describes')
    check.set_defaults(run=_check)
    contracts = verbs.add_parser(
        'contracts',
        parents=[common],
        help='list the contracts the policy shows',
        description='Every contract the policy shows, one line of each.',
    )
    contracts.set_defaults(run=_contracts)
    describe = verbs.add_parser(
        'describe',
        parents=[common],
        help='describe one contract: its slots, and the schemas of what it takes and gives',
        description='One contract whole: its slots, and the JSON Schemas of what it takes and gives.',
    )
    describe.add_argument('contract', help='a contract: its class name, or module:Class')
    describe.add_argument('--variant', metavar='TAG', help="one variant of the request's union, by its tag")
    describe.set_defaults(run=_describe)
    reads = argparse.ArgumentParser(add_help=False)
    reads.add_argument('contract', help='a contract: its class name, or module:Class')
    reads.add_argument('slots', nargs='*', metavar='slot=value', help='a slot to narrow by; left out, it widens')
    reads.add_argument('--timeout', type=_seconds, default=5.0, metavar='S', help='seconds to wait (default 5)')
    fetch = verbs.add_parser(
        'fetch',
        parents=[common, reads],
        help="read a retained contract's values",
        description="A retained contract's values: those the slots select, or every one.",
    )
    fetch.set_defaults(run=_fetch)
    query = verbs.add_parser(
        'query',
        parents=[common, reads],
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


def emit(result: Any, *, pretty: bool) -> None:  # noqa: ANN401 - any JSON-ready value
    """Write ``result`` to stdout as JSON: indented when asked, or on a terminal."""
    indent = 2 if pretty or sys.stdout.isatty() else None
    sys.stdout.write(json.dumps(result, indent=indent, ensure_ascii=False) + '\n')


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
    injected = {name: policy.resolve(name, source, base=path.parent) for name, source in sorted(loaded.inject.items())}
    mesh = loaded.mesh
    summary: dict[str, Any] = {
        'policy': str(path),
        'mesh': {'mode': mesh.mode.value, 'router': mesh.router, 'connect': list(mesh.connect)},
        'packages': list(loaded.protocol.packages),
        'contracts': len(catalog.contracts),
        'visible': {op.value: shown[op.value] for op in policy.Op if op.value in shown},
        'hidden': len(catalog.contracts) - len(visible),
        'bind': dict(sorted(loaded.bind.items())),
        'inject': {name: {'source': i.source, 'length': len(i.value)} for name, i in injected.items()},
    }
    if args.connect:
        started = time.perf_counter()
        open_session(mesh).close()
        summary['connect'] = {'ok': True, 'seconds': round(time.perf_counter() - started, 3)}
    emit(summary, pretty=args.pretty)
    return 0


def _contracts(args: argparse.Namespace) -> int:
    from .schemas import listing

    *_, rules = load(args.policy)
    emit(listing(rules), pretty=args.pretty)
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
        emit(describe_variant(contract, args.variant, rules), pretty=args.pretty)
    else:
        emit(describe(contract, rules), pretty=args.pretty)
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
    _finish(result, pretty=args.pretty)
    return 0


def _query(args: argparse.Namespace) -> int:
    from . import executor, policy, schemas

    path, loaded, catalog, rules = load(args.policy)
    contract = _readable(catalog, rules, args.contract, 'query')
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))
    request = _request(args.request) if args.request is not None else None
    names = schemas.injected(contract, rules)
    injected = {name: policy.resolve(name, loaded.inject[name], base=path.parent).value for name in names}
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
    _finish(result, pretty=args.pretty)
    return 0


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


def _finish(result: Any, *, pretty: bool) -> None:  # noqa: ANN401 - an executor.Result
    """Print a read's replies, and its notes on stderr."""
    emit(result.items, pretty=pretty)
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

    def line(value: Any) -> None:  # noqa: ANN401 - a JSON-ready value
        sys.stdout.write(json.dumps(value, ensure_ascii=False) + '\n')
        sys.stdout.flush()

    async def run(session: Any) -> executor.Result:  # noqa: ANN401 - a zenoh.Session
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
    for note in result.notes:
        sys.stderr.write(json.dumps({'note': note}) + '\n')
    return 0


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
