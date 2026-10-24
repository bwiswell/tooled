"""An app for the tests, as a policy's ``[app] factory`` names one: the game at the root, the service group twice."""

from __future__ import annotations

import asyncio
import threading
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
    if ctx.state is None:
        return 'up'
    return f'up, bound {sorted(ctx.state.bound)}, injected {sorted(ctx.state.injected)}'


def station_show(command: object, ctx: parsed.Context[State]) -> object:
    """A station, as the fixture's world has it: five in stock."""
    from _support_protocol import Station

    return Station(world='w1', station=command.station, stock=5)  # ty: ignore[unresolved-attribute]


def fleet_mine(command: object, ctx: parsed.Context[State]) -> str:
    """A fleet sent to mine."""
    return f'{command.fleet} mining'  # ty: ignore[unresolved-attribute]


def station_dock(command: object, ctx: parsed.Context[State]) -> parsed.Ask:
    """Docking, asked first."""
    station = command.station  # ty: ignore[unresolved-attribute]
    return parsed.Ask(f'Dock at {station} for 10 credits?', lambda: f'docked at {station}')


def run_fails(command: object, ctx: parsed.Context[State]) -> str:
    """A service that won't run."""
    msg = 'the run broke'
    raise RuntimeError(msg)


def factory() -> parsed.App:
    """The fixture app: the game at the root, the service group under ``relay`` and ``gateway``, with handlers."""
    from _support_tree import game, service

    app = parsed.App('demo')
    app.mount('_support_tree.game')
    app.mount('_support_tree.service', under='relay')
    app.mount('_support_tree.service', under='gateway')
    app.on(service.Status, '_support_app:status')
    app.on(service.Run, '_support_app:run_fails')
    app.on(game.StationShow, '_support_app:station_show')
    app.on(game.FleetMine, '_support_app:fleet_mine')
    app.on(game.StationDock, '_support_app:station_dock')
    return app


def orders(steps: list[object], ctx: parsed.Context[State]) -> str:
    """A fleet's chain of orders, run in turn."""
    return ' then '.join(type(step).__name__ for step in steps)


def chained() -> parsed.App:
    """The game at the root, and a fleet's orders as a chain."""
    from _support_tree import orders as fleet_orders

    app = factory()
    app.mount('_support_tree.orders', under='orders')
    app.on_chain(fleet_orders.FleetOrder, '_support_app:orders')
    return app


#: What lets the desk's viewer stop drawing: a test sets it, so its worker thread ends.
RELEASED = threading.Event()


def note_edit(command: object, ctx: parsed.Context[State]) -> str:
    """An editor that prompts a person for the note."""
    return input('the note: ')


def screen_show(command: object, ctx: parsed.Context[State]) -> str:
    """A viewer that draws until a key is pressed: here, until a test releases it."""
    RELEASED.wait(60)
    return 'drawn'


async def bell_ring(command: object, ctx: parsed.Context[State]) -> str:
    """A bell that rings until it's answered: awaited, so the loop goes on while it waits."""
    await asyncio.sleep(60)
    return 'answered'


#: The desk clock's reads so far; a test sets it back to 0.
TICKS = [0]


def clock_show(command: object, ctx: parsed.Context[State]) -> object:
    """The desk's clock, one tick on for each read."""
    from _support_tree.desk import Clock

    TICKS[0] += 1
    return Clock(tick=TICKS[0])


def desk() -> parsed.App:
    """An editor and a viewer, neither marked interactive, with handlers that wait on a person."""
    from _support_tree import desk as commands

    app = parsed.App('desk')
    app.mount('_support_tree.desk')
    app.on(commands.NoteEdit, '_support_app:note_edit')
    app.on(commands.ScreenShow, '_support_app:screen_show')
    app.on(commands.ClockShow, '_support_app:clock_show')
    app.on(commands.BellRing, '_support_app:bell_ring')
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
