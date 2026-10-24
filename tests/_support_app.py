"""An app for the tests, as a policy's ``[app] factory`` names one: the game at the root, the service group twice."""

from __future__ import annotations

from typing import Self

import parsed


class State:
    """The fixture app's state: the values the policy gave it, and whether a session has entered it, and left."""

    def __init__(self, bound: dict[str, str], injected: dict[str, str]) -> None:
        self.bound, self.injected = bound, injected
        self.entered = self.left = False
        self.left_with: BaseException | None = None

    def __enter__(self) -> Self:
        self.entered = True
        return self

    def __exit__(self, kind: object, error: BaseException | None, trace: object) -> None:
        self.left, self.left_with = True, error


#: The last state the fixture's state function built, for the tests to look at.
BUILT: list[State] = []


def state(*, bound: dict[str, str], injected: dict[str, str]) -> State:
    """The fixture's state function: a ``State`` of the values the policy gave."""
    BUILT.append(State(bound, injected))
    return BUILT[-1]


def no_keywords(values: dict[str, str]) -> State:
    """A state function that takes the policy's values the wrong way."""
    return State(values, {})


def broken_state(*, bound: dict[str, str], injected: dict[str, str]) -> State:
    """A state function that fails."""
    msg = f'no link today ({len(bound)} bound, {len(injected)} injected)'
    raise ConnectionError(msg)


class Unopenable:
    """A state that won't open."""

    def __enter__(self) -> Self:
        msg = 'the server is down'
        raise ConnectionError(msg)

    def __exit__(self, *args: object) -> None:
        pass


def unopenable(*, bound: dict[str, str], injected: dict[str, str]) -> Unopenable:
    """A state function whose state won't open."""
    return Unopenable()


def status(command: object, ctx: parsed.Context[State]) -> str:
    """The service's status, as the fixture app answers it: what its state was given."""
    return f'up, bound {sorted(ctx.state.bound)}, injected {sorted(ctx.state.injected)}'


def factory() -> parsed.App:
    """The fixture app: the game at the root, the service group under ``relay`` and ``gateway``."""
    from _support_tree import service

    app = parsed.App('demo')
    app.mount('_support_tree.game')
    app.mount('_support_tree.service', under='relay')
    app.mount('_support_tree.service', under='gateway')
    app.on(service.Status, '_support_app:status')
    return app


def nested() -> parsed.App:
    """The service group under a prefix of two words."""
    app = parsed.App('demo')
    app.mount('_support_tree.service', under='mesh relay')
    return app


def not_an_app() -> int:
    """A factory that returns something else."""
    return 5


def failing() -> parsed.App:
    """A factory that fails."""
    msg = 'no app today'
    raise RuntimeError(msg)


NOT_CALLABLE = 'an app'
