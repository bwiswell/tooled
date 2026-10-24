"""A fleet's orders as steps of one chain, joined by `then`: mounted by the fixture's chained app alone."""

from typing import ClassVar

import parsed


@parsed.parsed
class FleetOrder(parsed.Command):
    """A fleet's order: a step of its chain."""

    STEPS: ClassVar[bool] = True
    EFFECT = parsed.Effect.ACTION

    fleet: str = parsed.Str(required=True, doc="the fleet's key")


@parsed.parsed
class FleetJump(FleetOrder):
    """Jump a fleet to a system."""

    PHRASE = 'fleet {fleet} jump {system}'

    system: str = parsed.Str(required=True, doc="the system's key")


@parsed.parsed
class FleetDock(FleetOrder):
    """Dock a fleet at a station."""

    PHRASE = 'fleet {fleet} dock {station}'

    station: str = parsed.Str(required=True, doc="the station's key")
