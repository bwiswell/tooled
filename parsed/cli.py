"""The ``parsed`` command (``python -m parsed``).

Stdlib ``argparse`` only. For now it answers ``--version``, with the seared and zeared it runs on (a protocol
package parsed reads must pin the same zeared), and prints its help.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """The ``parsed`` argument parser."""
    parser = argparse.ArgumentParser(
        prog='parsed',
        description='Typed, policy-guarded access to a zeared mesh, for agents and people.',
    )
    parser.add_argument('--version', action='version', version=version())
    return parser


def version() -> str:
    """``parsed X (seared Y, zeared Z)``: parsed's version, and the seared and zeared it runs on."""
    import seared
    import zeared

    from . import __version__  # lazy: the package imports this module before it sets its version

    return f'parsed {__version__} (seared {seared.__version__}, zeared {zeared.__version__})'


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``parsed`` command.

    Args:
        argv: The arguments, without the program name; ``sys.argv[1:]`` when ``None``.

    Returns:
        The exit code.
    """
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
