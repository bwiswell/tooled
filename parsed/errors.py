"""What can go wrong, and the exit code each answers with.

Every failure the command reports is a :class:`ParsedError`. The command writes it to stderr as one JSON object:
``{"error": kind, "message": …, "path": […]}``, where ``path`` (when there is one) points into the input at fault,
and exits with the error's code. stdout carries results only.

| Exit | Kind |
|---|---|
| 2 | ``usage``, ``policy``, ``catalog``: the command, its policy file or its protocol can't be used as given |
| 3 | ``refused``: the policy refuses the call |
| 4 | ``validation``: the request doesn't load as its class: seared's error, with its path |
| 5 | ``mesh``: the session wouldn't open, or the link failed |
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Sequence


class ParsedError(Exception):
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


class UsageError(ParsedError):
    """The command was given something it can't use: an unknown contract, say, or a malformed slot."""

    kind = 'usage'
    exit_code = 2


class PolicyFileError(ParsedError):
    """The policy file can't be found, read or loaded, or names what the protocol doesn't have."""

    kind = 'policy'
    exit_code = 2


class CatalogError(ParsedError):
    """A protocol package won't import, or one of its contracts can't be read."""

    kind = 'catalog'
    exit_code = 2


class RefusedError(ParsedError):
    """The policy refuses the call."""

    kind = 'refused'
    exit_code = 3


class InvalidRequestError(ParsedError):
    """The request doesn't load as its class. The message is seared's, and the path names the field at fault."""

    kind = 'validation'
    exit_code = 4


class MeshError(ParsedError):
    """The mesh couldn't be reached: the session wouldn't open, or the link failed."""

    kind = 'mesh'
    exit_code = 5
