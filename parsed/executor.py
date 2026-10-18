"""The executor: the one session a command opens, from the policy's ``[mesh]``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .errors import MeshError

if TYPE_CHECKING:
    from zeared import SessionConfig

#: Zenoh appends where in its source it refused; the reason is what's useful.
_SOURCE = re.compile(r'\s+at /\S+$')


def open_session(config: SessionConfig) -> Any:  # noqa: ANN401 - a zenoh.Session, or a ManagedSession
    """Open the session the policy's ``[mesh]`` describes.

    Raises:
        MeshError: It wouldn't open, a router unreachable say. The message is zenoh's reason.
    """
    import zeared

    try:
        return zeared.open(config)
    except Exception as exc:  # zenoh raises its own error type, which parsed doesn't import
        msg = f'the session would not open: {_SOURCE.sub("", str(exc)).rstrip(". ")}'
        raise MeshError(msg) from exc
