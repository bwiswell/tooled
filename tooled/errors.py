"""What can go wrong, and the exit code each answers with.

Every failure the command reports is a :class:`TooledError`. The command writes it to stderr as one JSON object:
``{"error": kind, "message": …, "path": […]}``, where ``path`` (when there is one) points into the input at fault,
and exits with the error's code. stdout carries results only.

| Exit | Kind |
|---|---|
| 2 | ``usage``, ``policy``, ``catalog``: the command, its policy file or its protocol can't be used as given |
| 3 | ``refused``: the policy refuses the call |
| 4 | ``validation``: the request doesn't load as its class: seared's error, with its path |
| 5 | ``mesh``: the session wouldn't open, the link failed, or a write got no reply (with its generated values) |
| 6 | ``too_large``: the result is over the size cap; the error says its size and each part's |
| 7 | ``timeout``: ``wait`` ended before any value met its conditions; the error holds the last value seen |
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class TooledError(Exception):
    """A failure the command reports, with its kind, its exit code, and where in the input it lies.

    Args:
        message: What went wrong, for the reader.
        path: Where in the input it lies: keys and indexes, outermost first; empty when it's nowhere in particular.
    """

    kind: ClassVar[str] = 'error'
    exit_code: ClassVar[int] = 1

    def __init__(self, message: str, *, path: Sequence[str | int] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.path = tuple(path)

    def to_json(self) -> dict[str, Any]:
        """The error as the command writes it to stderr."""
        out: dict[str, Any] = {'error': self.kind, 'message': self.message}
        if self.path:
            out['path'] = list(self.path)
        return out


class UsageError(TooledError):
    """The command was given something it can't use: an unknown contract, say, or a malformed slot."""

    kind = 'usage'
    exit_code = 2


class PolicyFileError(TooledError):
    """The policy file can't be found, read or loaded, or names what the protocol doesn't have."""

    kind = 'policy'
    exit_code = 2


class CatalogError(TooledError):
    """A protocol package won't import, or one of its contracts can't be read."""

    kind = 'catalog'
    exit_code = 2


class RefusedError(TooledError):
    """The policy refuses the call."""

    kind = 'refused'
    exit_code = 3


class InvalidRequestError(TooledError):
    """The request doesn't load as its class. The message is seared's, and the path names the field at fault."""

    kind = 'validation'
    exit_code = 4


class MeshError(TooledError):
    """The mesh couldn't be reached: the session wouldn't open, or the link failed."""

    kind = 'mesh'
    exit_code = 5


class LostWriteError(MeshError):
    """A write that got no reply, after its retry if it had one: it may or may not have been carried out.

    Args:
        message: What was asked, and how long tooled waited.
        generated: The values generated for the request (an order's id, say), so the effect can be looked for.
    """

    def __init__(self, message: str, *, generated: Mapping[str, str]) -> None:
        super().__init__(message)
        self.generated = dict(generated)

    def to_json(self) -> dict[str, Any]:
        """The error, with the generated values when there are any."""
        out = super().to_json()
        if self.generated:
            out['generated'] = self.generated
        return out


class TooLargeError(TooledError):
    """The result is over the size cap, so it isn't printed. The error carries what to narrow it by.

    Args:
        message: What went wrong, and the ways round it.
        size: The result's size, in bytes.
        cap: The cap, in bytes.
        parts: The size of each part of the result, the biggest first.
    """

    kind = 'too_large'
    exit_code = 6

    def __init__(self, message: str, *, size: int, cap: int, parts: dict[str, Any]) -> None:
        super().__init__(message)
        self.size, self.cap, self.parts = size, cap, parts

    def to_json(self) -> dict[str, Any]:
        """The error, with the result's size, the cap, and each part's size."""
        return {**super().to_json(), 'size': self.size, 'cap': self.cap, 'parts': self.parts}


class TimedOutError(TooledError):
    """``wait`` ended before any value met its conditions.

    The error carries the last value seen, so the agent sees how far it got.

    Args:
        message: What was waited for, how long, and how many values were seen.
        last: The last value seen on a selected key, as ``wait`` prints one; ``None`` when there was none.
    """

    kind = 'timeout'
    exit_code = 7

    def __init__(self, message: str, *, last: Any) -> None:  # a JSON-ready value, or None
        super().__init__(message)
        self.last = last

    def to_json(self) -> dict[str, Any]:
        """The error, with the last value seen (``null`` for none)."""
        return {**super().to_json(), 'last': self.last}
