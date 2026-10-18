"""Loopback Zenoh for the tests: a peer that listens on a free TCP port, for a client to reach."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

import pytest
import zeared as z
import zenoh

zenoh.init_log_from_env_or('error')


def free_port() -> int:
    """A TCP port on loopback that nothing is listening on, as of now."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@dataclass
class Mesh:
    """A loopback peer listening at ``endpoint``, and its ``session``, for the test to serve from.

    ``asked`` holds each request the served ``LookReply`` and ``MoveResult`` were sent, in order.
    """

    endpoint: str
    session: zenoh.Session
    asked: list = field(default_factory=list)


@pytest.fixture
def mesh():
    """A loopback peer listening on a free TCP port."""
    endpoint = f'tcp/127.0.0.1:{free_port()}'
    config = zenoh.Config()
    config.insert_json5('mode', '"peer"')
    config.insert_json5('scouting/multicast/enabled', 'false')
    config.insert_json5('listen/endpoints', f'["{endpoint}"]')
    peer = zenoh.open(config)
    try:
        yield Mesh(endpoint, peer)
    finally:
        peer.close()


@pytest.fixture
def listening(mesh):
    """The listening peer's endpoint, ``tcp/127.0.0.1:<port>``."""
    return mesh.endpoint


@pytest.fixture
def world(mesh):
    """The fixture protocol served on the listening peer.

    - Retained: stations ``s1`` and ``s2`` of ``w1`` (stock 3 and 5), ``s1`` of ``w2``, and ``w1``'s status, with
      a logo (bytes).
    - ``LookReply`` answers what it was asked: the variant's kind and fields, the slot captures, and the params.
    - ``MoveResult`` answers a move, and refuses one to ``nowhere`` (``ok`` false).
    - ``EchoReply`` says back the text it's sent.

    Each look and move it's sent is kept in ``mesh.asked``.
    """
    import _support_protocol as demo

    for world_name, station, stock in (('w1', 's1', 3), ('w1', 's2', 5), ('w2', 's1', 7)):
        demo.Station(world=world_name, station=station, stock=stock).send(session=mesh.session)
    demo.Status(world='w1', up=True, logo=b'\x89PNG-ish').send(session=mesh.session)

    def look(ctx):
        mesh.asked.append(ctx.request)
        probe = ctx.request.probe
        seen = (
            f'{type(probe).__name__.lower()} {type(probe).dump(probe)} at {sorted(ctx.captures.items())} {ctx.params}'
        )
        return demo.LookReply(world=ctx.captures['world'], player=ctx.captures['player'], seen=seen)

    demo.LookReply.on_query(look, session=mesh.session)

    def move(ctx):
        mesh.asked.append(ctx.request)
        ok = ctx.request.to != 'nowhere'
        return demo.MoveResult(world=ctx.captures['world'], player=ctx.captures['player'], ok=ok)

    demo.MoveResult.on_query(move, session=mesh.session)
    demo.EchoReply.on_query(
        lambda ctx: demo.EchoReply(world=ctx.captures['world'], text=ctx.request.text), session=mesh.session
    )
    return mesh


@pytest.fixture(autouse=True)
def _reset_zeared_state():
    def _reset():
        z.session._set_default(None)
        z.clear_publisher_cache()
        z.clear_retention_cache()
        z.clear_queryable_cache()
        z.clear_presence_state()

    _reset()
    yield
    _reset()
