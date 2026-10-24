"""The tree: an app's session, opened on the state the policy builds for it.

A policy with ``[app]`` names the function that returns parsed's ``App``, whose mounts' commands the catalog reads,
and, with ``[app] state = "pkg.module:function"``, the one that builds the state its session opens on.

- **The state** (:func:`made_state`) is built by calling that function with the policy's values, as keyword
  arguments: ``bound``, every ``[bind]`` value by its slot, and ``injected``, every ``[inject]`` value by its name,
  each source read, and each generated one made once, as the state is built. What it does with them (a link to a
  server, a commander's presence) is the app's own business. Without ``[app] state``, the session opens on none.
- **The session** (:func:`opened`) is ``app.open(state)``, entered and left as parsed does it: a state that opens and
  closes (a context manager, as a link to a server is) is entered with the session and left after, and handlers see
  what entering it gave as ``ctx.state``. The session knows no terminal, parsed's default, so an ``INTERACTIVE``
  command is refused before it runs.

A state function that can't be found, or doesn't take ``bound`` and ``injected``, is the policy's mistake
(``catalog``, exit 2); one that fails when called, or a state that won't open, is the app's (``mesh``, exit 5, as
a session that won't open is).

**A line is judged before it runs** (:func:`judged`), against the app alone, so a refused line never opens the
session: it's read as the app reads it, then each of its steps (one, or a chain's) is held to the verb (``see`` runs
only reads, ``do`` anything else), to what the policy shows, and to its key: safe, and allowed for its op, and for
``confirm`` too where the line gives ``--yes`` on a step that asks first. parsed's own ``--yes`` would answer it
whatever tooled said, so a ``--yes`` the policy doesn't allow refuses the line. A line that doesn't read, or asks
for help, runs nothing: it isn't judged, and parsed answers it as it would.

**A line runs guarded** (:func:`ran`): an app may leave an editor or a viewer unmarked (``INTERACTIVE`` is optional),
and such a handler would wait on a person who isn't there. So it runs with no stdin (an empty one: a prompt reads
nothing, and fails at once) and a stdout of its own (stdout holds tooled's result; what a handler writes there is
kept out of it, and said), in a worker thread tooled waits on for its bound, ``--timeout``; past it, the line is
given up on, exit 7, and the worker is left to end with the process. A line that waits on its own (``--until``) is
given its own bound and a few seconds more, so parsed's timeout, with its last reply, comes first.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from parsed import ReadError

from .catalog import Command, imported, made_app
from .errors import CatalogError, MeshError, RefusedError, TimedOutError, UsageError
from .policy import Op, op_of

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator, Mapping
    from pathlib import Path

    from parsed import App, Result
    from parsed.app import Mount, Session
    from parsed.wait import Wait

    from .policy import Policy, Rules


def made_state(target: str, *, bound: Mapping[str, str], injected: Mapping[str, str]) -> Any:  # the app's state
    """The state ``target``'s function builds from the policy's ``bound`` and ``injected`` values.

    Raises:
        CatalogError: ``target`` names no function, or one that doesn't take ``bound`` and ``injected``.
        MeshError: The function failed.
    """
    found = imported(target, 'state')
    try:
        inspect.signature(found).bind(bound={}, injected={})
    except (TypeError, ValueError) as exc:
        msg = f'[app] state {target!r} takes the keyword arguments bound and injected: {exc}'
        raise CatalogError(msg, path=('app', 'state')) from exc
    try:
        return found(bound=dict(bound), injected=dict(injected))
    except Exception as exc:  # whatever building the state raises, it's the app's
        msg = f"the app's state would not build: [app] state {target!r} failed: {type(exc).__name__}: {exc}"
        raise MeshError(msg) from exc


def state_of(policy: Policy, path: Path) -> Any:  # the app's state, or None
    """The state the policy at ``path`` builds for its app: none without ``[app] state``.

    Raises:
        PolicyFileError: An inject source can't be read.
        CatalogError: As :func:`made_state`.
        MeshError: As :func:`made_state`.
    """
    from . import policy as policies

    if policy.app is None or policy.app.state is None:
        return None
    injected = {
        name: policies.resolve(name, source, base=path.parent).value for name, source in sorted(policy.inject.items())
    }
    return made_state(policy.app.state, bound=policy.bind, injected=injected)


def app_of(policy: Policy) -> App:
    """The app ``[app] factory`` returns.

    Raises:
        CatalogError: The policy has no ``[app]``, or the factory gives no app.
    """
    if policy.app is None:
        msg = 'the policy names no [app]'
        raise CatalogError(msg, path=('app',))
    return made_app(policy.app.factory)


@contextlib.asynccontextmanager
async def opened(app: App, state: Any) -> AsyncGenerator[Session[Any]]:  # state: the app's
    """``app``'s session, open on ``state``: entered here, and left when the block ends.

    Raises:
        MeshError: The session wouldn't open: entering the state failed.
    """
    session = app.open(state)
    try:
        entered = await session.__aenter__()
    except Exception as exc:  # whatever entering the app's state raises, it's the app's
        msg = f"the app's session would not open: {type(exc).__name__}: {exc}"
        raise MeshError(msg) from exc
    try:
        yield entered
    except BaseException as exc:  # the state is left knowing why, and the error goes on
        await session.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    await session.__aexit__(None, None, None)


#: Each tree op's verb, and the other's, to name in a refusal.
_VERBS = {Op.SEE: ('see', 'do'), Op.DO: ('do', 'see')}


@dataclass(frozen=True, slots=True)
class Line:
    """A line, read and judged, before it runs.

    Attributes:
        text: The line.
        commands: Each step's command, by the catalog: one, or a chain's.
        keys: Each step's key, as the policy judged it.
        given: The fields the line gives, by name, its first step's.
        yes: Whether it gives ``--yes``, which the policy allowed.
        wait: Its wait, when it gives ``--until`` (or ``--watch``) on a read that can wait; ``None`` otherwise.
    """

    text: str
    commands: tuple[Command, ...]
    keys: tuple[str, ...]
    given: tuple[str, ...]
    yes: bool
    wait: Wait | None = None


def judged(app: App, rules: Rules, text: str, *, op: Op) -> Line | None:
    """``text`` read as ``app`` reads it, and judged by ``rules`` for ``op``'s verb, before it runs.

    ``None`` for a line that runs nothing (one that doesn't read, or asks for help), which parsed answers itself.

    Raises:
        UsageError: A step is a command the verb doesn't run: a write under ``see``, a read under ``do``.
        RefusedError: A step the policy doesn't show, a key it refuses or can't hold, or a ``--yes`` where it
            doesn't allow ``confirm``.
    """
    try:
        routed = app.walk(text)
        if 'help' in routed.reading.own:
            return None
        steps = [step.load() for step in routed.reading.steps]
    except ReadError:
        return None  # it doesn't read, or a value is refused: parsed's own envelope says so
    yes = 'yes' in routed.reading.own
    commands, keys = [], []
    for instance in steps:
        command = _command_of(rules, routed.mount, type(instance))
        label = rules.catalog.label(command)
        if op_of(command) is not op:
            verb, other = _VERBS[op]
            what = 'a read' if op is Op.DO else 'no read'
            msg = f'{verb} does not run {label}, which is {what}; what does: {other}'
            raise UsageError(msg)
        if not rules.visible(command):
            raise RefusedError(rules.not_shown(command))
        key = rules.key(command, instance, routed.mount.resolvers)
        rules.check(command, key)
        if yes and command.doc.asks:
            try:
                rules.check(command, key, confirm=True)
            except RefusedError as exc:
                exc.message += '; leave --yes out, and its question comes back unanswered, nothing done'
                raise
        commands.append(command)
        keys.append(key)
    return Line(text, tuple(commands), tuple(keys), tuple(routed.reading.given), yes, routed.reading.wait)


def _command_of(rules: Rules, mount: Mount, cls: type) -> Command:
    """The catalog's command for ``cls``, as ``mount`` holds it."""
    for command in rules.catalog.commands:
        if command.cls is cls and command.under == mount.under:
            return command
    msg = f"{cls.__name__} under {mount.under!r} is no command of the policy's app"  # the catalog read this app
    raise UsageError(msg)


#: How much longer than a line's own wait tooled waits on it, so parsed's timeout, with its last reply, comes first.
GRACE = 5.0


def bound_of(line: Line | None, timeout: float) -> float | None:
    """How long tooled waits on a line: ``timeout``, or longer for a line that waits; ``None`` for a watch unbound."""
    wait = line.wait if line is not None else None
    if wait is None:
        return timeout
    if wait.timeout is None:
        return None
    return max(timeout, wait.timeout + GRACE)


def ran(app: App, state: Any, text: str, *, bound: float | None) -> tuple[Result, str]:  # state: the app's
    """``text`` run in ``app``'s session on ``state``, alone, in a worker thread waited on for ``bound``.

    Returns:
        What came of it, and what its handlers wrote to stdout, which isn't the result's.

    Raises:
        MeshError: The session wouldn't open (:func:`opened`).
        TimedOutError: No result within ``bound`` seconds: a handler still waiting, on a person perhaps. The worker
            is left behind, to end with the process.
    """
    done: dict[str, Any] = {}

    async def session() -> Result:
        async with opened(app, state) as open_session:
            return await open_session.run(text)

    def work() -> None:
        try:
            done['result'] = asyncio.run(session())
        except BaseException as exc:  # noqa: BLE001 -- handed to the caller's thread, which raises it
            done['error'] = exc

    worker = threading.Thread(target=work, name='tooled line', daemon=True)
    with _alone() as wrote:
        worker.start()
        worker.join(bound)
    if worker.is_alive():
        msg = (
            f'no result for {text!r} within {bound:g}s: a handler still waiting, on a person perhaps (a command not '
            'marked INTERACTIVE), was given up on; --timeout gives a line longer'
        )
        raise TimedOutError(msg, last=None)
    if 'error' in done:
        raise done['error']
    return done['result'], wrote.getvalue()


@contextlib.contextmanager
def _alone() -> Generator[io.StringIO]:
    """An empty stdin and a stdout of its own, while the block runs.

    A prompt reads nothing, and fails at once; what a handler writes is kept apart from tooled's result.
    """
    held = sys.stdin, sys.stdout
    wrote = io.StringIO()
    sys.stdin, sys.stdout = io.StringIO(''), wrote
    try:
        yield wrote
    finally:
        sys.stdin, sys.stdout = held
