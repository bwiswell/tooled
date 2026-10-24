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

**A watch streams** (:func:`watched`): a read with ``--watch`` gives its first reply, then one each time it changes,
until its count, its bound, its conditions, or a stop. Each result is handed from the worker to the caller's thread
as it comes, for the caller to write a line of; a stop (a signal, at the command line) cancels the watch, and the
session closes as it would. The session reads again every :data:`INTERVAL` seconds, since tooled can't know when an
app's state moves.

**In a session** (:class:`Held`), the app's session is opened once and held on a loop of its own thread, and each
line runs there as a task, guarded the same way: its handlers' output kept apart, its stdin empty (by the call's own
streams, :mod:`tooled.attached`), and a line past its bound cancelled, the session going on. A handler that blocks
the loop can't be cancelled, and holds every call on the session while it blocks: past a line's bound, when the loop
doesn't answer within :data:`GRACE`, the session is wedged, and each call on it fails.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import inspect
import io
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from parsed import ReadError

from . import attached
from .catalog import Command, imported, made_app
from .errors import CatalogError, MeshError, RefusedError, TimedOutError, UsageError
from .policy import Op, op_of

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Coroutine, Generator, Mapping
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
    session = app.open(state, interval=INTERVAL)
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


class Cold:
    """An app's session opened for one line, and closed after it: a cold call's, as :func:`ran` and :func:`watched`."""

    def __init__(self, app: App, state: Any) -> None:  # state: the app's
        self.app, self.state = app, state

    def ran(self, text: str, *, bound: float | None) -> tuple[Result, str]:
        """As :func:`ran`."""
        return ran(self.app, self.state, text, bound=bound)

    def watched(
        self, text: str, *, each: Callable[[Result], None], bound: float | None, stop: threading.Event | None = None
    ) -> tuple[Result | None, str, bool]:
        """As :func:`watched`."""
        return watched(self.app, self.state, text, each=each, bound=bound, stop=stop)


#: How much longer than a line's own wait tooled waits on it, so parsed's timeout, with its last reply, comes first.
GRACE = 5.0

#: How often, in seconds, a session reads a waiting line again: tooled can't know when an app's state moves.
INTERVAL = 1.0

#: How long a stopped watch's worker is given to close its session, in seconds, before it's left to the process.
_CLOSING = 2.0


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
        raise _given_up(text, bound)
    if 'error' in done:
        raise done['error']
    return done['result'], wrote.getvalue()


def _given_up(text: str, bound: float | None) -> TimedOutError:
    """A line given up on at its bound."""
    msg = (
        f'no result for {text!r} within {bound:g}s: a handler still waiting, on a person perhaps (a command not '
        'marked INTERACTIVE), was given up on; --timeout gives a line longer'
    )
    return TimedOutError(msg, last=None)


def watched(  # noqa: PLR0913 - one watch: what it runs on, and how it ends
    app: App,
    state: Any,  # the app's
    text: str,
    *,
    each: Callable[[Result], None],
    bound: float | None,
    stop: threading.Event | None = None,
) -> tuple[Result | None, str, bool]:
    """Each result of the watch ``text`` asks for, handed to ``each`` in the calling thread as it comes.

    It runs alone in a worker, as :func:`ran`'s line does, until the watch ends (its count, its bound, its
    conditions), ``bound`` passes, or ``stop`` is set, which cancels it and lets its session close.

    Returns:
        The last result (``None`` if none came), what handlers wrote to stdout, and whether the watch ended on its
        own rather than stopped.

    Raises:
        MeshError: The session wouldn't open.
        TimedOutError: ``bound`` passed with the watch still running. It carries the last result's envelope.
    """
    return _watching(_Watch(app, state, text), each=each, bound=bound, stop=stop)


def _watching(
    watch: _Watch, *, each: Callable[[Result], None], bound: float | None, stop: threading.Event | None
) -> tuple[Result | None, str, bool]:
    """Run ``watch``, cold or held, handing each result to ``each``: as :func:`watched` says."""
    deadline = time.monotonic() + bound if bound is not None else None
    last: Result | None = None
    with watch.alone() as wrote:
        watch.start()
        while (found := watch.take(stop, deadline)) is not None:
            last = found
            each(found)
    watch.reraise()
    stopped = stop is not None and stop.is_set()
    if deadline is not None and time.monotonic() > deadline and not stopped:
        msg = (
            f'the watch {watch.text!r} was still running at {bound:g}s, and was given up on; --timeout gives it longer'
        )
        raise TimedOutError(msg, last=last.to_json() if last is not None else None)
    return last, wrote.getvalue(), not stopped


class _Watch:
    """A watch, run in a worker thread: its results queued for the caller's thread, and a way to cancel it."""

    def __init__(self, app: App, state: Any, text: str) -> None:  # state: the app's
        self.text = text
        self.results: queue.Queue[Any] = queue.Queue()
        self.ended = object()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[Any] | None = None
        self.error: BaseException | None = None
        self.worker = threading.Thread(target=self._work, args=(app, state), name='tooled watch', daemon=True)

    def alone(self) -> contextlib.AbstractContextManager[io.StringIO]:
        """The streams the watch's handlers run with: an empty stdin, and a stdout of their own."""
        return _alone()

    def start(self) -> None:
        """Start it."""
        self.worker.start()

    def _join(self, timeout: float) -> None:
        self.worker.join(timeout)

    def _check(self) -> None:
        """Raise, while it runs, what ends every call on its session."""

    async def _streamed(self, session: Session[Any]) -> None:
        self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
        async with contextlib.aclosing(session.watch(self.text)) as stream:
            async for result in stream:
                self.results.put(result)

    async def _session(self, app: App, state: Any) -> None:
        async with opened(app, state) as session:
            await self._streamed(session)

    def _work(self, app: App, state: Any) -> None:
        try:
            asyncio.run(self._session(app, state))
        except asyncio.CancelledError:
            pass  # stopped: the session has closed
        except BaseException as exc:  # noqa: BLE001 -- handed to the caller's thread, which raises it
            self.error = exc
        finally:
            self.results.put(self.ended)

    def take(self, stop: threading.Event | None, deadline: float | None) -> Result | None:
        """The next result; or ``None`` once the watch has ended, or been cancelled for ``stop`` or ``deadline``."""
        while True:
            if (stop is not None and stop.is_set()) or (deadline is not None and time.monotonic() > deadline):
                self._cancel()
                self._join(_CLOSING)
                return None
            self._check()
            try:
                found = self.results.get(timeout=0.05)
            except queue.Empty:
                continue
            return None if found is self.ended else found

    def _cancel(self) -> None:
        if self.loop is not None and self.task is not None:
            with contextlib.suppress(RuntimeError):  # the loop has closed: the worker is done
                self.loop.call_soon_threadsafe(self.task.cancel)

    def reraise(self) -> None:
        """Raise what the worker raised, in the caller's thread."""
        if self.error is not None:
            raise self.error


class _HeldWatch(_Watch):
    """A watch on a held session: run as a task on its loop, its handlers' output kept by the call's own streams."""

    def __init__(self, held: Held, text: str) -> None:
        self.held, self.text, self.wrote = held, text, io.StringIO()
        self.results = queue.Queue()
        self.ended = object()
        self.loop = self.task = None
        self.error = None
        self.future: concurrent.futures.Future[None] | None = None

    def alone(self) -> contextlib.AbstractContextManager[io.StringIO]:
        """The buffer the watch's handlers write to: the call's stdout is theirs while the task runs."""
        return contextlib.nullcontext(self.wrote)

    def start(self) -> None:
        """Start it on the session's loop."""
        self.future = self.held.submit(self._held())

    def _join(self, timeout: float) -> None:
        if self.future is not None:
            concurrent.futures.wait([self.future], timeout)

    def _check(self) -> None:
        if self.held.dead is not None:
            self._cancel()
            raise MeshError(self.held.dead)

    async def _held(self) -> None:
        try:
            with attached.kept(self.wrote):
                await self._streamed(self.held.opened())
        except asyncio.CancelledError:
            pass  # stopped: the session stays open
        except BaseException as exc:  # noqa: BLE001 -- handed to the caller's thread, which raises it
            self.error = exc
        finally:
            self.results.put(self.ended)


#: How often a call waiting on a held session's line looks at its stop, and at the session, in seconds.
_POLL = 0.05


class Held:
    """An app's session held open on a loop of its own thread, for a session's calls (:mod:`tooled.server`).

    Each line runs on that loop as a task; between calls the loop goes on, so the state's own tasks (a heartbeat)
    run too.

    Args:
        app: The app.
        state: The state its session opens on.
        on_wedged: Told why, once a handler has blocked the loop past a line's bound.
    """

    def __init__(self, app: App, state: Any, *, on_wedged: Callable[[str], None] | None = None) -> None:
        self.app, self.state = app, state
        self.on_wedged = on_wedged
        self.dead: str | None = None
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name='tooled session', daemon=True)
        self._entered: contextlib.AbstractAsyncContextManager[Session[Any]] | None = None
        self._session: Session[Any] | None = None

    def open(self, *, timeout: float) -> None:
        """Open the app's session on its state, on the loop.

        Raises:
            MeshError: It wouldn't open (:func:`opened`), or not within ``timeout`` seconds.
        """
        self._thread.start()

        async def enter() -> None:
            self._entered = opened(self.app, self.state)
            self._session = await self._entered.__aenter__()

        future = asyncio.run_coroutine_threadsafe(enter(), self.loop)
        try:
            future.result(timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            self._halt()
            msg = f"the app's session did not open within {timeout:g}s"
            raise MeshError(msg) from exc
        except BaseException:
            self._halt()
            raise

    def opened(self) -> Session[Any]:
        """The app's session, open."""
        assert self._session is not None  # noqa: S101 -- lines run once it's open
        return self._session

    def close(self, *, timeout: float) -> None:
        """Close the app's session, so its state is left, and stop the loop."""
        try:
            if self._entered is not None and self.dead is None:
                asyncio.run_coroutine_threadsafe(self._entered.__aexit__(None, None, None), self.loop).result(timeout)
        finally:
            self._halt()

    def submit[T](self, work: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Run ``work`` on the session's loop, in the calling context (its call's streams among it)."""
        return asyncio.run_coroutine_threadsafe(work, self.loop)

    def responsive(self, within: float) -> bool:
        """Whether the loop runs a callback within ``within`` seconds: not, while a handler blocks it."""
        answered = threading.Event()
        try:
            self.loop.call_soon_threadsafe(answered.set)
        except RuntimeError:  # closed
            return False
        return answered.wait(within)

    def ran(self, text: str, *, bound: float | None) -> tuple[Result, str]:
        """``text`` run in the held session, waited on for ``bound``: as :func:`ran`'s line is.

        Raises:
            TimedOutError: No result within ``bound`` seconds; the line is cancelled, and the session goes on.
            MeshError: The session is wedged: by this line's handler, or another's.
            Stopped: The call's client stopped it, or went; the line is cancelled.
        """
        wrote = io.StringIO()

        async def line() -> Result:
            with attached.kept(wrote):
                return await self.opened().run(text)

        future = self.submit(line())
        stop = attached.stop()
        deadline = time.monotonic() + bound if bound is not None else None
        while not concurrent.futures.wait([future], _POLL).done:
            if self.dead is not None:
                future.cancel()
                raise MeshError(self.dead)
            if stop.is_set():
                future.cancel()
                raise attached.Stopped(stop.signum)
            if deadline is not None and time.monotonic() > deadline:
                future.cancel()
                if not self.responsive(GRACE):
                    raise MeshError(self._wedge(text))
                raise _given_up(text, bound)
        return future.result(), wrote.getvalue()

    def watched(
        self, text: str, *, each: Callable[[Result], None], bound: float | None, stop: threading.Event | None = None
    ) -> tuple[Result | None, str, bool]:
        """The watch ``text`` asks for, in the held session: as :func:`watched`'s."""
        return _watching(_HeldWatch(self, text), each=each, bound=bound, stop=stop)

    def _wedge(self, text: str) -> str:
        """Mark the session wedged by ``text``'s handler, and say so: why every call on it now fails."""
        self.dead = (
            f"the session's loop is blocked: a handler of {text!r} hasn't returned (a synchronous one, which can't "
            'be cancelled), and holds every call; the session ends, to be started again'
        )
        if self.on_wedged is not None:
            self.on_wedged(self.dead)
        return self.dead

    def _halt(self) -> None:
        """Stop the loop, what's left on it cancelled first; a thread a handler holds is left to the process."""

        async def drained() -> None:
            left = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in left:
                task.cancel()
            await asyncio.gather(*left, return_exceptions=True)
            await self.loop.shutdown_asyncgens()

        with contextlib.suppress(RuntimeError):  # already closed
            concurrent.futures.wait([asyncio.run_coroutine_threadsafe(drained(), self.loop)], _CLOSING)
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(_CLOSING)
        if not self._thread.is_alive():
            self.loop.close()


class _Split(io.TextIOBase):
    """stdout, split by thread: the main thread's writes (tooled's result) go out; any other's are kept apart."""

    def __init__(self, out: Any, kept: io.StringIO) -> None:  # out: the real stdout
        self.out, self.kept = out, kept

    def _here(self) -> Any:  # the stream this thread writes to
        return self.out if threading.current_thread() is threading.main_thread() else self.kept

    def write(self, text: str) -> int:
        """Write ``text`` where this thread's writes go."""
        return self._here().write(text)

    def flush(self) -> None:
        """Flush where this thread's writes go."""
        self._here().flush()

    def isatty(self) -> bool:
        """Whether this thread writes to a terminal: never, for a handler."""
        here = self._here()
        return here is self.out and bool(getattr(here, 'isatty', lambda: False)())


@contextlib.contextmanager
def _alone() -> Generator[io.StringIO]:
    """An empty stdin, and a stdout of the worker's own, while the block runs.

    A prompt reads nothing, and fails at once; what a handler writes is kept apart from tooled's result, which the
    main thread writes as before.
    """
    held = sys.stdin, sys.stdout
    wrote = io.StringIO()
    sys.stdin, sys.stdout = io.StringIO(''), _Split(held[1], wrote)
    try:
        yield wrote
    finally:
        sys.stdin, sys.stdout = held
