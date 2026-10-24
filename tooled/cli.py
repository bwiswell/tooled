"""The ``tooled`` command (``python -m tooled``).

Its command line is read by parsed: each verb is a command class (:mod:`tooled.verbs`), and this module carries it
out. Every verb takes ``--policy`` (else ``$TOOLED_POLICY``, else ``./tooled.toml``) after the verb, so a permission
rule can match a verb by its prefix (``tooled fetch``). ``--version`` names the seared, zeared and parsed tooled runs
on, since a protocol package tooled reads must pin the same zeared, and an app the same parsed. ``tooled --help``
lists the verbs, and ``tooled VERB --help`` says what one takes.

**Verbs:**
- ``check``: load the policy, its protocol and its app's commands, resolve what it injects, and summarize what it
  shows: how many commands it read, when it reads any; each source's length, and the path it read, variables
  expanded; and, as ``fixed``, each open slot the allow rules let only one value fill, which the policy would rather
  bind; and the shown commands that declare neither an effect nor that they're interactive. With ``--connect``, also
  open the session; with
  ``--no-resolve``, read no inject source and expand no variable, so a policy can be checked against its protocol
  without its secrets (in CI, say).
- ``operations``: every operation the policy shows, contracts and commands, one line of each: its route, its kind
  or its phrase, the verbs that act on it, its effect, its topic with the bound slots filled, the slots left to give,
  and its summary; an interactive command marked, with the one to say instead.
- ``describe OPERATION``: one operation, layered. A contract's slots, its request's schema (a root union folded to its
  tags), and its reply as a field list; ``--variant TAG`` gives one variant's. A command's phrases, keys, slots,
  flags, asking first, overlaps, arguments' schema and reply. ``--reply`` gives the reply's whole schema.
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

- ``tools [--format openai|anthropic|mcp] [--verbs VERBS] [--sizes]``: the operations the policy shows as tool
  definitions, for a harness to hand a model (:mod:`tooled.tools`): one for each fetch, query, write, wait or listen,
  and one for each allowed variant of a request's union; ``--verbs`` keeps those of some verbs, or of some verbs'
  operations (``fetch,wait:Fleet*``). ``--sizes`` gives
  each one's bytes and estimated tokens instead. A presence feed has none, and a note says which.
- ``call TOOL [--args JSON]``: carry out one call of a tool ``tools`` exported, as a model wrote it
  (:mod:`tooled.calls`): its arguments mapped back to the verb its name begins with, then checked and carried out as
  that verb is, and printed as it prints. A listen's values come as one JSON list. An argument the tool doesn't
  take, or a value it can't, is refused naming it, worded for the model the harness hands it back to.
- ``wait CONTRACT [slot=value …] --until PATH=VALUE [--until …]``: subscribe as ``listen`` does, until a value
  meets every condition, and print it: a retained contract already in that state answers at once. A path is dotted
  (``numbers.fuel``); a value reads as JSON if it can (``40``, ``true``, ``"30%"``), else as a string (``idle``).
  Past ``--timeout`` (30 s by default), or on SIGINT or SIGTERM, it exits 7 with the last value seen.

- ``session start | run | stop | status``: a session holding the policy's world open, for the calls that name it
  (:mod:`tooled.sessions`): ``start`` opens one and says where it is, ``run -- COMMAND`` opens one for a command,
  named in its ``$TOOLED_SESSION``, and closes it when the command ends, ``stop`` closes one, and ``status`` says how
  one is, or every one.

- ``serve [--session NAME] [--verbs VERBS]``: the tools the policy shows, over MCP on stdin and stdout, each call
  carried out in the session named, else in one of its own (:mod:`tooled.mcp`).

A call of any verb but ``check``, ``session``'s and ``serve``, given ``--session`` (else ``$TOOLED_SESSION``), runs
in that session, on the mesh and the app it holds, and prints what it would cold.

A verb renamed is refused naming its new name (``contracts`` is ``operations`` since 0.2.4). A slot left out
widens to every value; a bound one is filled by the policy. ``--timeout`` bounds a fetch, a query
or an invoke (5 s by default), and a wait (30 s).

**Output.** Results go to stdout as JSON, indented on a terminal or with ``--pretty``. ``--select`` keeps parts of
a result; a result over the cap (``--max-bytes``, else ``$TOOLED_MAX_BYTES``, else 28,000 bytes) is refused with
the size of each part; ``--out FILE`` writes it whole. A failure goes to stderr as one JSON object, ``{"error":
kind, "message": …, "path": […]}``, and the exit code says which kind (see :mod:`tooled.errors`). A note on
stderr (``{"note": …}``) says what a read left out, and a library's log line goes there as JSON too (``{"log":
level, …}``).

**The call log.** With ``TOOLED_LOG`` set to a path, each run appends one JSON line to it when it ends: the verb,
what it acted on, the names of what was given, the exit code and error kind, the bytes on stdout and the seconds
taken, and no value (:mod:`tooled.ledger`).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import time
from typing import TYPE_CHECKING, Any, NoReturn

from .errors import TooledError, UsageError

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Generator, Sequence
    from pathlib import Path

    from zeared import SessionConfig

    from .catalog import Catalog, Contract
    from .ledger import Entry
    from .policy import Policy, Rules


#: How many of a level's names a refused path's hint lists, when none is close.
_HINT_NAMES = 12

#: How often a listen or a wait in a session looks at its call's stop, in seconds.
_STOP_POLL = 0.05

#: The verbs, as the parser has them.
VERBS = (
    'check',
    'operations',
    'describe',
    'fetch',
    'query',
    'listen',
    'invoke',
    'wait',
    'tools',
    'call',
    'see',
    'do',
    'session',
    'serve',
)

#: The verbs that take a line: tooled's own options first, then the line's words, each as the shell gave it.
LINES = ('see', 'do')

#: The flags a line's verb takes before its line that take a value, as :class:`tooled.verbs.Lined` declares them:
#: the words a call's session is looked for among, before the line, short of reading the arguments with parsed.
_LINE_VALUES = frozenset({'--policy', '--select', '--max-bytes', '--out', '--session', '--timeout'})

#: Verbs renamed, refused naming the new one rather than as unknown verbs.
RENAMED = {
    'contracts': '`tooled contracts` was renamed `tooled operations` in tooled 0.2.4',
}


def version() -> str:
    """``tooled X (seared Y, zeared Z, parsed W)``: tooled's version, and the seared, zeared and parsed it runs on."""
    import parsed
    import seared
    import zeared

    from . import __version__  # lazy: the package imports this module before it sets its version

    on = f'seared {seared.__version__}, zeared {zeared.__version__}, parsed {parsed.__version__}'
    return f'tooled {__version__} ({on})'


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``tooled`` command.

    Args:
        argv: The arguments, without the program name; ``sys.argv[1:]`` when ``None``.

    Returns:
        The exit code.
    """
    from . import sessions
    from .ledger import Entry

    given = list(sys.argv[1:] if argv is None else argv)
    entry = Entry.begin(given, VERBS)
    handle = _session_of(given)
    code = sessions.attach(given, handle, entry) if handle else ran(given, entry)
    _log_call(entry)
    return code


def ran(given: list[str], entry: Entry) -> int:
    """One run of the command, as :func:`main` makes it cold and a session makes it for a call: its exit code.

    The arguments are read by parsed (:func:`tooled.verbs.read`), and the verb's runner carries them out. Help and the
    version are written to stdout, exit 0. An error is written to stderr, and ends ``entry`` with it; no line is
    logged here.
    """
    from . import verbs
    from .sessions import failed

    try:
        if given and given[0] in RENAMED:
            raise UsageError(RENAMED[given[0]])
        read = verbs.read(given)
        if read.command is None:
            text = version() + '\n' if read.version else read.help or ''
            sys.stdout.write(text)
            entry.wrote(text)
            entry.end(0)
            return 0
        args = _args(read)
        args.entry = entry
        entry.arguments = [slot.partition('=')[0] for slot in getattr(args, 'slots', None) or []]
        _log_as_json()
        code = args.run(args)
    except TooledError as exc:
        return failed(exc, entry)
    entry.end(code)
    return code


def _args(read: Any) -> argparse.Namespace:  # a verbs.Given, holding a command
    """A verb's command, read, as the namespace its runner takes: each field by its name, read on as it's used.

    Paths are strings, ``--select``'s paths and ``--until``'s conditions parsed, ``--verbs``' picks split, a line's
    verb's line put together, ``session run``'s command split into its words, and ``--dry-run`` a switch, as
    parsed's own flag given ``invoke``.
    """
    from . import verbs

    command = read.command
    args = argparse.Namespace(select=None, max_bytes=None, out=None)
    for spec in type(command).__seared_fields__:
        setattr(args, spec.attr, getattr(command, spec.attr))
    for name in ('policy', 'out'):
        if getattr(args, name, None) is not None:
            setattr(args, name, str(getattr(args, name)))
    if args.select is not None:
        from .output import parse_paths

        args.select = parse_paths(args.select)
    if isinstance(command, verbs.Wait):
        from .executor import Condition

        args.until = [Condition.parse(text) for text in command.until]
    if isinstance(command, verbs.Tools | verbs.Serve):
        from .tools import CALLS

        picked = tuple(pick.strip() for pick in command.verbs.split(',')) if command.verbs is not None else None
        args.verbs = picked if picked is not None or isinstance(command, verbs.Serve) else CALLS
    if isinstance(command, verbs.Lined):
        args.line = read.line
    if isinstance(command, verbs.SessionRun):
        args.command = read.words
    if isinstance(command, verbs.Invoke):
        args.dry_run = 'dry-run' in read.own
    args.verb, args.run = verbs.verb_of(type(command)), _runner(type(command))
    return args


def _runner(cls: type) -> Callable[[argparse.Namespace], int]:
    """The function that carries out a verb, by its command class."""
    from . import mcp, sessions, verbs

    runners: dict[type, Callable[[argparse.Namespace], int]] = {
        verbs.Check: _check,
        verbs.Operations: _operations,
        verbs.Describe: _describe,
        verbs.Fetch: _fetch,
        verbs.Query: _query,
        verbs.Listen: _listen,
        verbs.Invoke: _invoke,
        verbs.Wait: _wait,
        verbs.Tools: _tools,
        verbs.Call: _call,
        verbs.See: _see,
        verbs.Do: _do,
        verbs.Serve: mcp.serve,
        verbs.SessionStart: sessions.start,
        verbs.SessionRun: sessions.run,
        verbs.SessionStop: sessions.stop,
        verbs.SessionStatus: sessions.status,
    }
    return runners[cls]


def _session_of(given: list[str]) -> str | None:
    """The session a call attaches to: ``--session``'s value, else ``$TOOLED_SESSION``; ``None`` for none.

    Only a verb that attaches has one, and a line's verb only among tooled's own options, before its line. An empty
    value names none. The arguments are looked through here without parsed, which an attached call would otherwise
    import only for this (:data:`_LINE_VALUES`); the session reads them whole.
    """
    from .sessions import ATTACHES, SESSION_ENV

    if not given or given[0] not in ATTACHES:
        return None
    named, at, lined = None, 1, given[0] in LINES
    while at < len(given):
        word = given[at]
        name, eq, value = word.partition('=')
        if word == '--' or (lined and not word.startswith('-')):
            break  # the verb's flags end, or a line's first word begins it
        if name == '--session':
            named = value if eq else given[at + 1] if at + 1 < len(given) else named
        at += 2 if lined and name in _LINE_VALUES and not eq else 1
    if named is None:
        named = os.environ.get(SESSION_ENV, '')
    return named or None


def _log_call(entry: Entry) -> None:
    """Append the run's line to the call log, if there is one; a failure to is a note, not the run's."""
    from .ledger import append

    note = append(entry)
    if note:
        _notes([note])


def _entry(args: argparse.Namespace) -> Entry:
    """The run's call-log entry; a throwaway when a runner is called without :func:`main`."""
    from .ledger import Entry

    entry = getattr(args, 'entry', None)
    return entry if entry is not None else Entry()


def emit(result: Any, args: argparse.Namespace) -> None:  # any JSON-ready value
    """Write ``result`` to stdout as JSON, after ``--select``.

    Indented when asked or on a terminal; written to ``--out``'s file instead, when given; refused when over the cap.
    """
    from .output import as_json, check_size, max_bytes, select, write_out

    if args.select:
        result = select(result, args.select, held=getattr(args, 'unsure', None))
    text = as_json(result, indent=2 if args.pretty or sys.stdout.isatty() else None)
    out = getattr(args, 'out', None)
    if out:
        text = as_json(write_out(out, text), indent=None)
    else:
        check_size(text, result, cap=max_bytes(args.max_bytes))
    sys.stdout.write(text + '\n')
    _entry(args).wrote(text + '\n')


def load(given: str | None) -> tuple[Path, Policy, Catalog, Rules]:
    """The policy file, the policy, its protocol's catalog, and its rules over that catalog: in a session, its own."""
    from . import attached, policy
    from .catalog import Catalog

    held = attached.held()
    if held is not None:
        return held.loaded(given)
    path = policy.find(given)
    loaded = policy.load(path)
    factory = loaded.app.factory if loaded.app is not None else None
    catalog = Catalog.load(loaded.protocol.packages, trees=loaded.protocol.trees, factory=factory)
    return path, loaded, catalog, policy.Rules(loaded, catalog)


def _check(args: argparse.Namespace) -> int:
    from . import policy

    path, loaded, catalog, rules = load(args.policy)
    shown: dict[str, list[str]] = {}
    visible = rules.visible_operations()
    for operation in visible:
        shown.setdefault(policy.op_of(operation).value, []).append(catalog.label(operation))
    fixed = {catalog.label(operation): slots for operation in visible if (slots := rules.fixed(operation))}
    inject: dict[str, Any] = {}
    for name, source in sorted(loaded.inject.items()):
        if policy.generated(source):
            inject[name] = {'source': policy.describe(source)}  # made for each call: nothing to read, or show
        elif args.no_resolve:
            inject[name] = {'source': policy.describe(source), 'resolved': False}
        else:
            found = policy.resolve(name, source, base=path.parent)
            inject[name] = {'source': found.source, 'length': len(found.value)}
            if found.path is not None:  # a path is no secret, and its variables expanded are worth seeing
                inject[name]['path'] = found.path
    mesh = loaded.mesh
    summary: dict[str, Any] = {
        'policy': str(path),
        'mesh': {'mode': mesh.mode.value, 'router': mesh.router, 'connect': list(mesh.connect)} if mesh else None,
        'packages': list(loaded.protocol.packages),
        **({'app': loaded.app.factory} if loaded.app is not None else {}),
        **({'state': loaded.app.state} if loaded.app is not None and loaded.app.state is not None else {}),
        **({'trees': list(loaded.protocol.trees)} if loaded.protocol.trees else {}),
        **({'commands': len(catalog.commands)} if loaded.app is not None or loaded.protocol.trees else {}),
        **({'unmarked': _unmarked(visible, catalog)} if catalog.commands else {}),
        'operations': len(catalog.operations),
        'visible': {op.value: shown[op.value] for op in policy.Op if op.value in shown},
        'hidden': len(catalog.operations) - len(visible),
        'bind': dict(sorted(loaded.bind.items())),
        'fixed': fixed,
        **_overlaps(rules),
        'inject': inject,
    }
    if args.connect:
        _connect(path, loaded, summary)
    emit(summary, args)
    return 0


def _unmarked(visible: Sequence[Any], catalog: Catalog) -> list[str]:  # contracts and commands
    """The shown commands that declare neither ``EFFECT`` nor ``INTERACTIVE``: the ones a profile may want to deny.

    An undeclared command counts as a write, and an unmarked one might wait on a person who isn't there.
    """
    from .catalog import Command

    return [catalog.label(o) for o in visible if isinstance(o, Command) and o.effect is None and not o.doc.interactive]


def _overlaps(rules: Rules) -> dict[str, Any]:
    """``check``'s overlaps, when there are any, and the denies that don't carry over.

    Each command and contract that do the same thing, how, the side the policy takes and the one it shows; and each
    deny on a contract that can't carry over to its command, which stays shown.
    """
    if not rules.links:
        return {}
    label = rules.catalog.label
    overlaps = []
    for link in rules.links:
        shown = [label(o) for o in (link.command, link.contract) if rules.visible(o)]
        overlaps.append(
            {
                'command': label(link.command),
                'contract': label(link.contract),
                'by': list(link.by),
                'route': rules.winner(link).value,
                'shown': shown[0] if shown else None,
            }
        )
    uncarried = [
        {'deny': index, 'contract': label(link.contract), 'command': label(link.command)}
        for index, link in rules.uncarried()
    ]
    return {'overlaps': overlaps, 'uncarried': uncarried}


def _operations(args: argparse.Namespace) -> int:
    from .schemas import listing

    *_, rules = load(args.policy)
    emit(listing(rules), args)
    return 0


def _tools(args: argparse.Namespace) -> int:
    from . import tools

    *_, catalog, rules = load(args.policy)
    found = tools.tools(rules, args.verbs)
    emit(tools.sizes(found, args.format) if args.sizes else [tools.spec(tool, args.format) for tool in found], args)
    untooled = [catalog.label(contract) for contract in tools.untooled(rules)]
    if untooled:
        _notes([f'no tool for a presence feed, which no verb acts on yet: {", ".join(untooled)}'])
    interactive = [catalog.label(command) for command in tools.interactive(rules)]
    if interactive:
        _notes([f'no tool for an interactive command, for a person at a terminal: {", ".join(interactive)}'])
    return 0


def _call(args: argparse.Namespace) -> int:
    from . import calls, tools
    from .verbs import ASKED

    entry = _entry(args)
    entry.tool = args.tool
    *_, rules = load(args.policy)
    tool = tools.find(rules, args.tool)
    entry.acted_on(rules.catalog.label(tool.operation), variant=tool.variant, route=_route(tool))
    given = _request(args.args, what='--args') if args.args is not None else {}
    entry.arguments = list(given)
    mapped = calls.arguments(tool, given, rules)
    if _route(tool) == 'tree':
        return _call_line(args, rules, tool, mapped)
    slots = [f'{name}={value}' for name, value in mapped.slots.items()]
    run = argparse.Namespace(**vars(args), contract=tool.operation.qualname, slots=slots, timeout=ASKED, meta=False)
    if tool.verb == 'fetch':
        return _fetch(run)
    if tool.verb in {'query', 'invoke'}:
        run.request, run.param, run.dry_run, run.no_retry = json.dumps(mapped.request), [], False, False
        return _query(run) if tool.verb == 'query' else _invoke(run)
    if tool.verb == 'wait':
        run.until, run.timeout = list(mapped.until), mapped.timeout
        return _wait(run)
    return _listened(run, count=mapped.count or 1, duration=mapped.timeout or tools.TIMEOUT)


def _route(tool: Any) -> str:  # a Tool
    """The route a tool takes: the tree's for a command's, the wire's for a contract's."""
    from .catalog import Command

    return 'tree' if isinstance(tool.operation, Command) else 'wire'


def _call_line(args: argparse.Namespace, rules: Rules, tool: Any, mapped: Any) -> int:  # a Tool; its Arguments
    """A command's tool call: said as a line, in the mount that holds it, and run as ``see`` or ``do`` runs one."""
    from parsed import RenderError

    from .policy import Op
    from .verbs import LINE_BOUND

    app = _app(rules.policy)
    command = tool.operation
    mount = next(m for m in app.mounts if m.under == command.under and m.holds(command.cls))
    try:
        line = app.render(mapped.said, mount=mount)
    except RenderError as exc:
        msg = f'{tool.name}: {exc}'
        raise UsageError(msg) from exc
    if mapped.yes:
        line += ' --yes'
    if mapped.until:  # a read's wait: its conditions and its bound, as the line's own flags
        from parsed.argv import line_of

        words = [word for condition in mapped.until for word in ('--until', condition.text)]
        line += ' ' + line_of([*words, '--timeout', f'{mapped.timeout:g}s'])
    run = argparse.Namespace(**vars(args), line=line, timeout=LINE_BOUND)
    return _line(run, Op.DO if tool.verb == 'do' else Op.SEE)


def _see(args: argparse.Namespace) -> int:
    from .policy import Op

    return _line(args, Op.SEE)


def _do(args: argparse.Namespace) -> int:
    from .policy import Op

    return _line(args, Op.DO)


def _line(args: argparse.Namespace, op: Any) -> int:  # a tree op
    """Judge the line by the policy, then run it in the app's session, and print parsed's envelope.

    Its exit code is the envelope's: 0, or its error kind's, as tooled's are (2 a line that doesn't read, 3 refused,
    4 a value refused, 7 a wait past its bound), and 1 for a handler that fails. A line still running at its bound
    is given up on, exit 7 (:func:`tooled.tree.ran`).
    """
    from . import tree

    if not args.line:
        msg = f"tooled {args.verb} takes a line: the words of a command, after tooled's own options"
        raise UsageError(msg)
    text = args.line
    path, loaded, catalog, rules = load(args.policy)
    if loaded.app is None:
        msg = f"tooled {args.verb} runs a line of the policy's app, and the policy names no [app]"
        raise UsageError(msg)
    from . import attached

    app = _app(loaded)
    judged = tree.judged(app, rules, text, op=op)
    entry = _entry(args)
    if judged is not None:
        entry.acted_on(catalog.label(judged.commands[0]), route='tree')
        entry.arguments = entry.arguments or list(judged.given)  # a tool call's are its arguments
    held = attached.held()
    lines = held.lines if held is not None else tree.Cold(app, tree.state_of(loaded, path))
    bound = tree.bound_of(judged, args.timeout)
    if judged is not None and judged.wait is not None and judged.wait.watch:
        return _watched(args, lines, text, bound=bound)
    result, wrote = lines.ran(text, bound=bound)
    emit(result.to_json(), args)
    _notes(_wrote(wrote))
    return result.code


def _app(loaded: Policy) -> Any:  # a parsed App
    """The policy's app: in a session, the one it holds."""
    from . import attached, tree

    held = attached.held()
    return held.app if held is not None else tree.app_of(loaded)


def _watched(args: argparse.Namespace, lines: Any, text: str, *, bound: float | None) -> int:  # tree.Cold or Held
    """A line that watches: each envelope as a line of JSON as it comes, until the watch ends, or a signal.

    Each is written as ``listen`` writes a value, after ``--select``, one over the cap left out and noted. The exit
    code is the last envelope's, or 0 when a signal stopped it (in a session, the call's client asking).
    """
    from . import attached

    args.meta = False
    line = _Lines(args)
    stop = attached.stop()
    with _signalled(stop):
        last, wrote, ended = lines.watched(text, each=lambda result: line(result.to_json()), bound=bound, stop=stop)
    _notes([*line.notes(), *_wrote(wrote)])
    return last.code if ended and last is not None else 0


def _wrote(wrote: str) -> list[str]:
    """The note for what a line's handlers wrote to stdout, kept out of the result: none, if they wrote nothing."""
    return [f"the line's handler wrote {len(wrote)} characters to stdout, kept out of the result"] if wrote else []


@contextlib.contextmanager
def _signalled(stop: Any) -> Generator[None]:  # a threading.Event
    """``stop`` set on SIGINT or SIGTERM while the block runs, on the main thread; elsewhere, the program's own."""
    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    held = {signum: signal.signal(signum, lambda *_: stop.set()) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for signum, handler in held.items():
            signal.signal(signum, handler)


def _describe(args: argparse.Namespace) -> int:
    from .catalog import Command, Contract
    from .errors import RefusedError
    from .schemas import describe, describe_command, describe_variant

    if args.variant is not None and args.reply:
        msg = "tooled describe takes --variant or --reply, not both: a variant's request, or the reply's schema"
        raise UsageError(msg)
    *_, catalog, rules = load(args.policy)
    operation = catalog.get(args.operation)
    label = catalog.label(operation)
    _entry(args).acted_on(label)
    if not isinstance(operation, Contract | Command):
        msg = f'{label} is no operation tooled describes'
        raise UsageError(msg)
    if not rules.visible(operation):
        raise RefusedError(rules.not_shown(operation))
    if isinstance(operation, Command):
        if args.variant is not None:
            msg = f"{label} is a command, whose arguments hold no union's variants: describe it whole"
            raise UsageError(msg)
        emit(describe_command(operation, rules, reply=args.reply), args)
    elif args.variant is not None:
        emit(describe_variant(operation, args.variant, rules), args)
    else:
        emit(describe(operation, rules, reply=args.reply), args)
    return 0


def _fetch(args: argparse.Namespace) -> int:
    from . import executor

    _, loaded, catalog, rules = load(args.policy)
    contract = _readable(args, catalog, rules, 'fetch')
    _judge(contract, catalog, args)
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))
    executor.check_fetch(contract, rules, values)  # before the session: a refusal never waits on the mesh
    with _meshed(loaded) as session:
        result = executor.fetch(session, contract, rules, values, timeout=args.timeout)
    _finish(result, args)
    return 0


def _query(args: argparse.Namespace) -> int:
    from . import executor

    path, loaded, catalog, rules = load(args.policy)
    contract = _readable(args, catalog, rules, 'query')
    _judge(contract, catalog, args)
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))
    params = _pairs(args.param, 'param')
    request = _request(args.request) if args.request is not None else None
    injected = _injected(path, loaded, contract, rules)
    call = executor.prepare(contract, rules, values, request=request, injected=injected)  # before the session
    _entry(args).acted_on(catalog.label(contract), variant=call.variant)
    with _meshed(loaded) as session:
        result = executor.query(
            session,
            contract,
            rules,
            values,
            request=request,
            injected=injected,
            params=params,
            timeout=args.timeout,
        )
    _finish(result, args)
    return 0


def _invoke(args: argparse.Namespace) -> int:
    from . import executor, policy

    path, loaded, catalog, rules = load(args.policy)
    contract = _readable(args, catalog, rules, 'invoke')
    _judge(contract, catalog, args)  # the reply's paths, a dry run's too: it checks the command that will be sent
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))
    request = _request(args.request) if args.request is not None else None
    injected = _injected(path, loaded, contract, rules)
    generated = [name for name in injected if policy.generated(loaded.inject[name])]
    call = executor.prepare(contract, rules, values, request=request, injected=injected)  # before any session
    _entry(args).acted_on(catalog.label(contract), variant=call.variant)
    if args.dry_run:
        secrets = [name for name in injected if name not in generated]
        emit(executor.preview(contract, call, hidden=secrets), argparse.Namespace(**{**vars(args), 'select': None}))
        _notes(['a dry run: checked, and not sent'])
        return 0
    with _meshed(loaded) as session:
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
    _finish(result, args, sent=True)
    return 0


def _connect(path: Path, loaded: Policy, summary: dict[str, Any]) -> None:
    """``check --connect``: open the policy's mesh, and its app's session on the state it builds, then close them.

    Raises:
        UsageError: The policy names neither.
    """
    import asyncio

    from .executor import open_session
    from .tree import app_of, opened, state_of

    if loaded.mesh is None and loaded.app is None:
        msg = 'the policy names no [mesh] and no [app], so --connect has nothing to open'
        raise UsageError(msg)
    if loaded.mesh is not None:
        started = time.perf_counter()
        open_session(loaded.mesh).close()
        summary['connect'] = {'ok': True, 'seconds': round(time.perf_counter() - started, 3)}
    if loaded.app is not None:
        app, state = app_of(loaded), state_of(loaded, path)

        async def visit() -> None:
            async with opened(app, state):
                pass

        started = time.perf_counter()
        asyncio.run(visit())
        summary['session'] = {'ok': True, 'seconds': round(time.perf_counter() - started, 3)}


@contextlib.contextmanager
def _meshed(loaded: Policy) -> Generator[Any]:  # a zenoh.Session, or a ManagedSession
    """The policy's mesh, opened for the call and closed after it; in a session, the one it holds, left open.

    Raises:
        UsageError: The policy names no ``[mesh]``.
        MeshError: It wouldn't open.
    """
    from . import attached, executor

    held = attached.held()
    if held is not None and held.mesh is not None:
        yield held.mesh
        return
    session = executor.open_session(_mesh(loaded))
    try:
        yield session
    finally:
        session.close()


def _mesh(loaded: Policy) -> SessionConfig:
    """The policy's mesh, for a verb that reaches it.

    Raises:
        UsageError: The policy names none, as one of commands alone may.
    """
    if loaded.mesh is None:
        msg = 'the policy names no [mesh], so nothing it allows reaches one'
        raise UsageError(msg)
    return loaded.mesh


def _injected(path: Path, loaded: Policy, contract: Contract, rules: Rules) -> dict[str, str]:
    """The values the policy injects into ``contract``'s request: each source read, each generated value made.

    In a session, a source was read once, as it started; a generated value is still made for each call.
    """
    from . import attached, policy, schemas

    names = schemas.injected(contract, rules)
    read = held.injected if (held := attached.held()) is not None else {}
    return {
        name: read[name] if name in read else policy.resolve(name, loaded.inject[name], base=path.parent).value
        for name in names
    }


def _judge(contract: Contract, catalog: Catalog, args: argparse.Namespace, *, until: Sequence[Any] = ()) -> None:
    """Walk ``--select``'s paths (and ``wait``'s ``--until`` paths) against the reply's schema, before the call.

    A path the schema doesn't have is refused here, before anything reaches the mesh. A field it declares may be
    absent from a value, so such a path is never held to matching; one it can't judge (``args.unsure``) still is.

    Raises:
        UsageError: A path names no field of the reply.
    """
    from seared.doc import json_schema

    from .schemas import ITEMS, walk

    schema = json_schema(contract.cls, mode='dump')
    label = catalog.label(contract)
    args.unsure = []
    for path in args.select or []:
        parts = [part for name, each in path for part in (name, ITEMS)[: 2 if each else 1]]
        found = walk(schema, parts)
        if found.declared is False:
            _refuse('--select', parts, label, found)
        args.unsure.append(found.declared is None)
    for condition in until:  # executor.Conditions
        found = walk(schema, condition.path, indexes=True)
        if found.declared is False:
            _refuse('--until', list(condition.path), label, found)


def _refuse(flag: str, parts: Sequence[str], label: str, found: Any) -> NoReturn:  # a schemas.Walk
    """Refuse a path that names no field: what it names, and what's near where it left the schema."""
    import difflib

    from .errors import UsageError
    from .schemas import ITEMS

    def text(chunk: Sequence[str]) -> str:
        out = ''
        for part in chunk:
            out += '[]' if part == ITEMS else ('.' if out else '') + part
        return out

    where, missing = text(parts[: found.at]), parts[found.at]
    if missing == ITEMS:
        hint = f'{where!r} is no list'
    elif found.listed:
        step = f'{where}[].{missing}' if flag == '--select' else f'{where}.0.{missing}'
        hint = f'{where!r} is a list: {step}'
    elif found.names:
        close = difflib.get_close_matches(missing, found.names, n=3, cutoff=0.6)
        names = ', '.join(found.names[:_HINT_NAMES]) + (', …' if len(found.names) > _HINT_NAMES else '')
        at = f'of {where!r}' if where else 'there'
        hint = f'did you mean {" or ".join(map(repr, close))}?' if close else f'the fields {at}: {names}'
    else:
        hint = f'{where!r} has no fields'
    msg = f'{flag} {text(parts)!r} names no field of {label}; {hint}'
    raise UsageError(msg, path=[part for part in parts[: found.at + 1] if part != ITEMS])


def _readable(args: argparse.Namespace, catalog: Catalog, rules: Rules, verb: str) -> Contract:
    """The contract ``args.contract`` names, if the policy shows it and ``verb`` acts on it; the call log notes it."""
    from .errors import RefusedError, UsageError
    from .schemas import verbs

    contract = catalog.contract(args.contract)
    label = catalog.label(contract)
    _entry(args).acted_on(label)
    if not rules.visible(contract):
        raise RefusedError(rules.not_shown(contract))
    if verb not in verbs(contract):
        acts = ', '.join(verbs(contract)) or 'nothing in this version of tooled'
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


def _request(text: str, *, what: str = 'the request') -> dict[str, Any]:
    """``--request`` (or ``call``'s ``--args``): JSON, ``-`` for stdin, or ``@path`` for a file; one JSON object."""
    from . import attached
    from .errors import UsageError

    source = what
    if text == '-':
        text, source = sys.stdin.read(), f'{what} on stdin'
    elif text.startswith('@'):
        try:
            text, source = attached.path(text[1:]).read_text(encoding='utf-8'), text[1:]
        except OSError as exc:
            msg = f'could not read {what} from {text[1:]}: {exc.strerror or exc}'
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


def _finish(result: Any, args: argparse.Namespace, *, sent: bool = False) -> None:  # an executor.Result
    """Print a call's replies, and its notes on stderr.

    Once a write is ``sent``, nothing about the output may read as the write not having happened: a ``--select``
    path the schema couldn't judge, matching nothing, is a note rather than an error, and a result over the cap
    says the write was answered.
    """
    from .errors import TooLargeError

    items, notes = result.items, list(result.notes)
    if sent:
        items, missed = _lenient(items, args)
        notes += missed
        args = argparse.Namespace(**{**vars(args), 'select': None})
    try:
        emit(items, args)
    except TooLargeError as exc:
        if not sent:
            raise
        exc.message = f'the write was sent and answered, but {exc.message}'
        _notes(notes)
        raise
    _notes(notes)


def _notes(notes: Sequence[str]) -> None:
    """Write each note to stderr, as one JSON line."""
    for note in notes:
        sys.stderr.write(json.dumps({'note': note}) + '\n')


def _lenient(value: Any, args: argparse.Namespace, *, meta: bool = False) -> tuple[Any, list[str]]:  # JSON-ready
    """``--select``'s parts of a result there's no taking back (a write sent, a wait over).

    A path the schema couldn't judge (``args.unsure``) that matches nothing is a note; the rest were judged before.
    """
    from .output import path_text

    if not args.select or value == []:
        return value, []
    value, found = _picked(value, args.select, meta=meta)
    held = getattr(args, 'unsure', None) or [True] * len(args.select)
    missed = [path for path, hit, hold in zip(args.select, found, held, strict=True) if hold and not hit]
    return value, [f'--select {path_text(path)!r} matched nothing' for path in missed]


def _listen(args: argparse.Namespace) -> int:
    line = _Lines(args)
    result = _subscribe(args, each=line, count=args.count, duration=args.duration)
    _notes([*result.notes, *line.notes()])
    return 0


def _listened(args: argparse.Namespace, *, count: int, duration: float) -> int:
    """A listen tool's call: the values, as one JSON list, once ``count`` have come or ``duration`` is up."""
    got: list[Any] = []
    result = _subscribe(args, each=got.append, count=count, duration=duration)
    result.items = got
    if len(got) < count:
        result.notes.insert(0, f'{len(got)} of the {count} values asked for came within {duration:g}s')
    _finish(result, args)
    return 0


def _subscribe(
    args: argparse.Namespace, *, each: Callable[[Any], None], count: int | None, duration: float | None
) -> Any:  # an executor.Result
    """Listen as ``args`` asks, handing each value to ``each``, until ``count`` values, ``duration``, or a signal."""
    import asyncio

    from . import executor

    _, loaded, catalog, rules = load(args.policy)
    contract = _readable(args, catalog, rules, 'listen')
    _judge(contract, catalog, args)
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))

    async def run(session: Any) -> executor.Result:  # a zenoh.Session
        with _stopped_by_signals() as stop:
            return await executor.listen(
                session,
                contract,
                rules,
                values,
                each=each,
                meta=args.meta,
                count=count,
                duration=duration,
                stop=stop,
            )

    with _meshed(loaded) as session:
        return asyncio.run(run(session))


def _wait(args: argparse.Namespace) -> int:
    import asyncio

    from . import executor
    from .errors import TimedOutError

    _, loaded, catalog, rules = load(args.policy)
    contract = _readable(args, catalog, rules, 'wait')
    _judge(contract, catalog, args, until=args.until)
    values = executor.slot_values(contract, rules, _pairs(args.slots, 'slot'))

    async def run(session: Any) -> executor.Result:  # a zenoh.Session
        with _stopped_by_signals() as stop:
            return await executor.wait(
                session, contract, rules, values, until=args.until, meta=args.meta, within=args.timeout, stop=stop
            )

    try:
        with _meshed(loaded) as session:
            result = asyncio.run(run(session))
    except TimedOutError as exc:
        exc.message, exc.last = _last_shown(exc.message, exc.last, args)
        raise
    value, missed = _lenient(result.items[0], args, meta=args.meta)
    emit(value, argparse.Namespace(**{**vars(args), 'select': None}))
    _notes([*result.notes, *missed])
    return 0


def _last_shown(message: str, last: Any, args: argparse.Namespace) -> tuple[str, Any]:  # a JSON-ready value
    """A timed-out wait's last value, after ``--select``, and left out (the message says so) when over the cap."""
    from .output import as_json, max_bytes

    if last is None:
        return message, None
    if args.select:
        last, _found = _picked(last, args.select, meta=args.meta)
    cap, size = max_bytes(args.max_bytes), len(as_json(last, indent=None).encode())
    if cap and size > cap:
        note = f'the last value seen is {size:,} bytes, over the {cap:,}-byte cap, so it is left out'
        note += ': --select keeps parts of it'
        return f'{message}; {note}', None
    return message, last


def _picked(value: Any, paths: Any, *, meta: bool) -> tuple[Any, list[bool]]:  # a JSON-ready value; parsed paths
    """``--select``'s parts of one value, or of the value inside its ``--meta`` wrapper; and which paths matched."""
    from .output import pick

    if not meta:
        return pick(value, paths)
    picked, found = pick(value['value'], paths)
    return {**value, 'value': picked}, found


@contextlib.contextmanager
def _stopped_by_signals() -> Generator[asyncio.Event]:
    """An event SIGINT or SIGTERM sets, while the block runs on the running loop; in a session, the call's stop.

    A loop takes signal handlers only on the main thread; elsewhere (tooled run inside another program) the
    program's own handling stands, and the verb's own bound ends it. A call in a session is stopped by its client,
    which sets the call's stop on the first signal it gets, or goes.
    """
    import asyncio
    import signal
    import threading

    from . import attached

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    call = attached.current()
    relayed = loop.create_task(_relayed(call.stop, stop)) if call is not None else None
    signals = (signal.SIGINT, signal.SIGTERM) if threading.current_thread() is threading.main_thread() else ()
    for signum in signals:
        loop.add_signal_handler(signum, stop.set)
    try:
        yield stop
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)
        if relayed is not None:
            relayed.cancel()


async def _relayed(given: Any, stop: asyncio.Event) -> None:  # given: a threading.Event
    """Set ``stop`` once ``given``, set from another thread, is."""
    import asyncio

    while not given.is_set():  # noqa: ASYNC110 -- a thread's event; waiting on it in another would hold the loop's end
        await asyncio.sleep(_STOP_POLL)
    stop.set()


class _Lines:
    """``listen``'s writer: each value, after ``--select``, as one flushed JSON line; one over the cap is left out.

    A path the schema couldn't judge that matches nothing in one value doesn't end the stream; :meth:`notes` says
    which never matched.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        from .output import max_bytes

        self.args, self.paths, self.meta = args, args.select or [], args.meta
        self.cap = max_bytes(args.max_bytes)
        self.matched = [False] * len(self.paths)
        self.over = 0

    def __call__(self, value: Any) -> None:  # a JSON-ready value
        from .output import as_json

        if self.paths:
            value, found = _picked(value, self.paths, meta=self.meta)
            self.matched = [a or b for a, b in zip(self.matched, found, strict=True)]
        text = as_json(value, indent=None)
        if self.cap and len(text.encode()) > self.cap:
            self.over += 1
            return
        sys.stdout.write(text + '\n')
        sys.stdout.flush()
        _entry(self.args).wrote(text + '\n')

    def notes(self) -> list[str]:
        """What the writer left out, and the paths that matched nothing."""
        from .output import path_text

        out = (
            [f'{self.over} values over the {self.cap:,}-byte cap were left out; --select narrows them']
            if self.over
            else []
        )
        held = getattr(self.args, 'unsure', None) or [True] * len(self.paths)  # judged once the call began
        out += [
            f'--select {path_text(path)!r} matched nothing in the stream'
            for path, found, hold in zip(self.paths, self.matched, held, strict=True)
            if hold and not found
        ]
        return out


class _JsonLogHandler(logging.Handler):
    """A log record as one JSON line on stderr, so stderr stays one JSON object a line."""

    def emit(self, record: logging.LogRecord) -> None:
        line = {'log': record.levelname.lower(), 'logger': record.name, 'message': record.getMessage()}
        sys.stderr.write(json.dumps(line) + '\n')


def _log_as_json() -> None:
    """Send warnings and worse, from tooled's libraries, to stderr as JSON lines (once)."""
    root = logging.getLogger()
    if not any(isinstance(handler, _JsonLogHandler) for handler in root.handlers):
        root.addHandler(_JsonLogHandler(logging.WARNING))
