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
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import TYPE_CHECKING, Any

from .errors import ParsedError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .catalog import Catalog
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
