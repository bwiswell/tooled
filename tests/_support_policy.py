"""Policy files for the tests, written to a temporary directory, over the fixture protocol."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: The head every test policy shares: a client mesh, the fixture protocol.
HEAD = """
[mesh]
mode = "client"
router = "tcp/127.0.0.1:7447"

[protocol]
packages = ["_support_protocol"]
"""


def write(directory: Path, body: str = '', *, head: str = HEAD, name: str = 'tooled.toml') -> Path:
    """Write ``head`` and ``body`` as a policy file in ``directory``, and return its path."""
    path = directory / name
    path.write_text(textwrap.dedent(head) + textwrap.dedent(body), encoding='utf-8')
    return path
