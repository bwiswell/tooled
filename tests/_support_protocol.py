"""A small zeared protocol for the tests: one contract of each kind parsed sorts by, on ``demo/{world}/…``."""

from __future__ import annotations

from typing import ClassVar

import zeared


@zeared.zeared
class Ping(zeared.Zeared):
    """Ask a station how it is."""

    station: str = zeared.Str(required=True, doc="the station's key")


@zeared.zeared
class Pong(zeared.Zeared):
    """Ask for a number back."""

    times: int = zeared.Int(default=1, doc='how many times')


@zeared.zeared
class Peek(zeared.Zeared):
    """A developer's look behind the curtain."""

    depth: int = zeared.Int(default=1, doc='how far')


@zeared.zeared
class LookRequest(zeared.Zeared):
    """A look, by kind, with the player's token."""

    token: str = zeared.Str(required=True, doc="the player's token")
    probe: object = zeared.Union(variants={'ping': Ping, 'pong': Pong, 'dev-peek': Peek}, tag_key='kind', doc='what')
    ask_id: str | None = zeared.Str(default=None, doc='an id for this look, to tell a repeat by')


@zeared.zeared
class MoveRequest(zeared.Zeared):
    """A move, with the player's token."""

    token: str = zeared.Str(required=True, doc="the player's token")
    to: str = zeared.Str(required=True, doc='where')


@zeared.zeared
class EchoRequest(zeared.Zeared):
    """Anything, said back."""

    text: str = zeared.Str(required=True, doc='what to say')


@zeared.zeared
class Station(zeared.Message):
    """A station's state, retained."""

    TOPIC: ClassVar[str] = 'demo/{world}/station/{station}'
    RETAINED: ClassVar[bool] = True

    world: str = zeared.Str(required=True, doc="the world's name")
    station: str = zeared.Str(required=True, doc="the station's key")
    stock: int = zeared.Int(default=0, doc='units in stock')


@zeared.zeared
class Status(zeared.Message):
    """The world's status, retained, and its presence."""

    TOPIC: ClassVar[str] = 'demo/{world}/status'
    RETAINED: ClassVar[bool] = True
    LIVELINESS: ClassVar[bool] = True

    world: str = zeared.Str(required=True, doc="the world's name")
    up: bool = zeared.Bool(default=True, doc='whether it is up')
    logo: bytes | None = zeared.Bytes(default=None, doc="the world's logo, as an image")


@zeared.zeared
class Notice(zeared.Message):
    """A line for a player, as it happens."""

    TOPIC: ClassVar[str] = 'demo/{world}/notice/{player}'

    world: str = zeared.Str(required=True, doc="the world's name")
    player: str = zeared.Str(required=True, doc="the player's key")
    text: str = zeared.Str(required=True, doc='what happened')


@zeared.zeared
class Trace(zeared.Message):
    """A trace line under any path."""

    TOPIC: ClassVar[str] = 'demo/{world}/trace/{path**}'

    text: str = zeared.Str(required=True, doc='the line')


@zeared.zeared
class Seen(zeared.Message):
    """Who is online: a presence feed and nothing else."""

    TOPIC: ClassVar[str] = 'demo/{world}/seen/{player}'
    LIVELINESS: ClassVar[bool] = True


@zeared.zeared
class LookReply(zeared.Message):
    """What a look saw: a read."""

    TOPIC: ClassVar[str] = 'demo/{world}/look/{player}'
    EFFECT = zeared.Effect.READ
    REQUEST: ClassVar[type | None] = LookRequest

    world: str = zeared.Str(required=True, doc="the world's name")
    player: str = zeared.Str(required=True, doc="the player's key")
    seen: str = zeared.Str(default='', doc='what was seen')


@zeared.zeared
class MoveResult(zeared.Message):
    """Where a move left the player: a write."""

    TOPIC: ClassVar[str] = 'demo/{world}/move/{player}'
    EFFECT = zeared.Effect.WRITE
    REQUEST: ClassVar[type | None] = MoveRequest

    world: str = zeared.Str(required=True, doc="the world's name")
    player: str = zeared.Str(required=True, doc="the player's key")
    ok: bool = zeared.Bool(required=True, doc='whether it moved')


@zeared.zeared
class EchoReply(zeared.Message):
    """An echo: a queryable that declares no effect."""

    TOPIC: ClassVar[str] = 'demo/{world}/echo'
    REQUEST: ClassVar[type | None] = EchoRequest

    text: str = zeared.Str(required=True, doc='what was said')
