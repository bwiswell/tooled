"""Loopback Zenoh for the tests: a peer that listens on a free TCP port, for a client to reach."""

from __future__ import annotations

import socket

import pytest
import zeared as z
import zenoh

zenoh.init_log_from_env_or('error')


def free_port() -> int:
    """A TCP port on loopback that nothing is listening on, as of now."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@pytest.fixture
def listening():
    """A loopback peer listening on a free TCP port; yields its endpoint, ``tcp/127.0.0.1:<port>``."""
    endpoint = f'tcp/127.0.0.1:{free_port()}'
    config = zenoh.Config()
    config.insert_json5('mode', '"peer"')
    config.insert_json5('scouting/multicast/enabled', 'false')
    config.insert_json5('listen/endpoints', f'["{endpoint}"]')
    peer = zenoh.open(config)
    try:
        yield endpoint
    finally:
        peer.close()


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
