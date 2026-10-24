"""Sessions for the tests: policies of the fixture app, the desk and the served world, and a session on one, held
by a thread of the test's own process.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import threading
from pathlib import Path

import _support_app
import pytest
from _support_policy import write

import tooled.tree
from tooled.server import Server
from tooled.sessions import Where

#: The fixture app, with chains, its state built from the policy; reads and writes shown, docking at vesta confirmed.
HEAD = '[protocol]\n\n[app]\nfactory = "_support_app:chained"\nstate = "_support_app:state"\n'
BODY = (
    '[bind]\nfleet = "f1"\n\n[inject.ticket]\nenv = "DEMO_TICKET"\n\n'
    '[[allow]]\nops = ["see", "do"]\n\n[[allow]]\nops = ["confirm"]\nkeys = ["dock/vesta"]\n'
)

#: The desk: an editor and a viewer with handlers that wait on a person, and a clock to watch.
DESK = '[protocol]\n\n[app]\nfactory = "_support_app:desk"\n\n[[allow]]\nops = ["see", "do"]\n'


@pytest.fixture
def runtime(monkeypatch):
    """A runtime directory short enough for a socket's path, and no session named in the environment."""
    base = Path(tempfile.mkdtemp(prefix='tooled-'))
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(base))
    monkeypatch.delenv('TOOLED_SESSION', raising=False)
    monkeypatch.setenv('DEMO_TICKET', 'tk-1')
    yield base
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def policy(tmp_path, runtime):
    return str(write(tmp_path, BODY, head=HEAD))


@contextlib.contextmanager
def serving(policy, *, name='t', idle=None, tied=None):
    """A session on ``policy``, served by a thread of this process, ended when the block does."""
    server = Server(Where.of(name), Path(policy).resolve(), idle=idle, tied=tied)
    server.open()
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.end('stopped')
        thread.join(30)
        assert not thread.is_alive()


@pytest.fixture
def desk(tmp_path, runtime, monkeypatch):
    monkeypatch.setattr(tooled.tree, 'INTERVAL', 0.05)
    _support_app.TICKS[0] = 0
    return str(write(tmp_path, DESK, head=''))


#: A policy over the served fixture world: reads, looks and moves at w1, the player's token injected, a move's id made.
WIRE = """
[bind]
world = "w1"

[inject.token]
env = "DEMO_TOKEN"

[inject.move_id]
generate = "uuid"

[[allow]]
ops = ["read", "query", "invoke"]
keys = ["demo/w1/**"]

[[deny]]
ops = ["query"]
variants = ["dev-*"]
"""


@pytest.fixture
def wire(tmp_path, runtime, world, monkeypatch):
    from _support_policy import HEAD as MESH

    monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
    return str(write(tmp_path, WIRE, head=MESH.replace('tcp/127.0.0.1:7447', world.endpoint)))
