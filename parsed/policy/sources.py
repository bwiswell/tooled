"""Inject sources: where a request field's value comes from, read only when it's needed.

A value read from a source is never written anywhere: not to output, not to an error, not to ``check``'s summary,
which shows only its source and its length. A literal value in the policy file isn't a source, so a secret can't
end up there.

A **generated** value is made fresh for each call (``generate = "uuid"``): an id a server can tell a repeat by,
say. It is no secret: a reply may echo it, and a lost write's error names it.
"""

from __future__ import annotations

import os
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import PolicyFileError

if TYPE_CHECKING:
    from .config import Source


@dataclass(frozen=True, slots=True)
class Injected:
    """A resolved value, and where it came from. Its ``repr`` leaves the value out."""

    name: str
    source: str
    value: str = field(repr=False)


def generated(source: Source) -> bool:
    """Whether ``source`` makes its value fresh for each call, rather than reading it."""
    return source.generate is not None


def describe(source: Source) -> str:
    """Where ``source`` reads from, in words; never the value."""
    if source.generate is not None:
        return f'generate {source.generate}'
    if source.env is not None:
        return f'env {source.env}'
    if source.file is not None:
        return f'file {source.file}'
    where = f'toml {source.toml}'
    if source.table is not None:
        where += f' [[{source.table}]]'
        if source.match:
            where += ' where ' + ', '.join(f'{k} = {v!r}' for k, v in source.match.items())
    return f'{where}, key {source.key}'


def resolve(name: str, source: Source, *, base: Path) -> Injected:
    """Read ``source``'s value (or make it, for a generated one), for the field ``name``.

    Args:
        name: The field the value is injected into.
        source: Where it comes from.
        base: The directory a relative path is read from: the policy file's own.

    Raises:
        PolicyFileError: The variable is unset, the file is missing, or the TOML has no one value there. The
            message names the source, never a value.
    """
    where = ('inject', name)
    if source.generate is not None:
        return Injected(name, describe(source), uuid.uuid4().hex)
    if source.env is not None:
        value = os.environ.get(source.env)
        if not value:
            msg = f'inject {name!r}: ${source.env} is not set'
            raise PolicyFileError(msg, path=where)
        return Injected(name, describe(source), value)
    if source.file is not None:
        text = _read(_path(source.file, base), name, where)
        value = text.strip()
        if not value:
            msg = f'inject {name!r}: {source.file} is empty'
            raise PolicyFileError(msg, path=where)
        return Injected(name, describe(source), value)
    return Injected(name, describe(source), _from_toml(name, source, base, where))


def _path(text: str, base: Path) -> Path:
    path = Path(text).expanduser()
    return path if path.is_absolute() else base / path


def _read(path: Path, name: str, where: tuple[str, ...]) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError as exc:
        msg = f'inject {name!r}: could not read {path}: {exc.strerror or exc}'
        raise PolicyFileError(msg, path=where) from exc


def _from_toml(name: str, source: Source, base: Path, where: tuple[str, ...]) -> str:
    toml, key = source.toml, source.key
    if toml is None or key is None:  # the policy's checks make a source with no env or file a whole toml one
        msg = f'inject {name!r}: a source is env, file, or toml with its key'
        raise PolicyFileError(msg, path=where)
    path = _path(toml, base)
    try:
        data = tomllib.loads(_read(path, name, where))
    except tomllib.TOMLDecodeError as exc:
        msg = f'inject {name!r}: {path} is not valid TOML: {exc}'
        raise PolicyFileError(msg, path=where) from exc
    holder: Any = data
    if source.table is not None:
        tables = data.get(source.table)
        if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
            msg = f'inject {name!r}: {path} has no array of tables [[{source.table}]]'
            raise PolicyFileError(msg, path=where)
        picked = [t for t in tables if all(t.get(k) == v for k, v in source.match.items())]
        if len(picked) != 1:
            msg = f'inject {name!r}: {len(picked)} of [[{source.table}]] in {path} match, not one'
            raise PolicyFileError(msg, path=where)
        holder = picked[0]
    value = holder.get(key)
    if not isinstance(value, str) or not value:
        msg = f'inject {name!r}: {path} has no string {key!r} there'
        raise PolicyFileError(msg, path=where)
    return value
