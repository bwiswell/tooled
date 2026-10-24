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
"""

from __future__ import annotations

import contextlib
import inspect
from typing import TYPE_CHECKING, Any

from .catalog import imported, made_app
from .errors import CatalogError, MeshError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from pathlib import Path

    from parsed import App
    from parsed.app import Session

    from .policy import Policy


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
