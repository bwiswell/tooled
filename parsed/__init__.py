"""``parsed``: typed, policy-guarded access to a zeared mesh, for agents and people.

It reads a zeared protocol package and a policy file. Each contract the policy allows becomes a command with
a JSON Schema, and every call is checked against the policy, and against the contract's own types, before
anything reaches the mesh.

The command is the surface (:func:`main`, the ``parsed`` script, ``python -m parsed``). The modules behind it
are shaped like a library, but promise no Python API before 1.0.
"""

from __future__ import annotations

from .cli import main

__version__ = '0.0.0'

__all__ = ['__version__', 'main']
