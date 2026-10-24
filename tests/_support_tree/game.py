"""A game's commands, at the root."""

from typing import ClassVar

import parsed
from _support_protocol import MoveRequest, Station


@parsed.parsed
class StationShow(parsed.Command):
    """Show a station: its stock, and why it's closed when it is."""

    PHRASE = 'station {station} show'
    EFFECT = parsed.Effect.READ
    REPLY = Station

    station: str = parsed.Str(required=True, doc="the station's key")


@parsed.parsed
class PlayerMove(parsed.Command, MoveRequest):
    """Move the player: the protocol's move, said as a line."""

    PHRASE = 'move {to}'
    EFFECT = parsed.Effect.WRITE


@parsed.parsed
class FleetMine(parsed.Command):
    """Send a fleet to mine where it is, until it's full."""

    PHRASE = 'fleet {fleet} mine'
    EXTRA_PHRASES: ClassVar[tuple[str, ...]] = ('mine with {fleet}',)

    fleet: str = parsed.Str(required=True, doc="the fleet's key")


@parsed.parsed
class StationMap(parsed.Command):
    """Draw a station's map, for a person at the client."""

    PHRASE = 'station {station} map'
    EFFECT = parsed.Effect.READ
    INTERACTIVE = True
    INSTEAD = StationShow

    station: str = parsed.Str(required=True, doc="the station's key")


@parsed.parsed
class StationDock(parsed.Command):
    """Dock at a station, asking first, since docking costs a fee."""

    PHRASE = 'dock {station}'
    EFFECT = parsed.Effect.ACTION
    ASKS = True
    KINDS: ClassVar[dict[str, parsed.Kind]] = {'station': parsed.Name('station')}

    station: str = parsed.Str(required=True, doc="the station's key")
