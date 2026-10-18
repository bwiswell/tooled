"""Inject sources: where a request field's value comes from, read only when it's needed.

A value read from a source is never written anywhere: not to output, not to an error, not to ``check``'s summary,
which shows only its source and its length. A literal value in the policy file isn't a source, so a secret can't
end up there.

A **generated** value is made fresh for each call (``generate = "uuid"``): an id a server can tell a repeat by,
say. It is no secret: a reply may echo it, and a lost write's error names it.

A ``file`` or ``toml`` **path** may hold variables: ``${NAME}``, or ``${NAME:-default}``, the default when ``NAME``
is unset or empty, as in a shell (``${XDG_CONFIG_HOME:-~/.config}/farlandia/identity.toml``). They're expanded as
the source is read, then a leading ``~``; a relative result is read from the policy file's directory. An unset
variable with no default is refused, never read as empty. A path is no secret: ``check`` shows the one it read.
"""

from __future__ import annotations

import os
import re
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import PolicyFileError

if TYPE_CHECKING:
    from .config import Source


#: A variable in a source's path: ``${NAME}``, or ``${NAME:-default}``.
VARIABLE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}')

#: A ``$`` that starts no variable this syntax reads: a bare ``$NAME``, or a ``${`` that isn't one.
_STRAY = re.compile(r'\$(\{|[A-Za-z_][A-Za-z0-9_]*)')


@dataclass(frozen=True, slots=True)
class Injected:
    """A resolved value, where it came from, and the path it read, if any. Its ``repr`` leaves the value out."""

    name: str
    source: str
    value: str = field(repr=False)
    path: str | None = None


def path_problem(text: str) -> str | None:
    """What's wrong with the variables in a source's path, if anything, read without expanding them."""
    for match in VARIABLE.finditer(text):
        if match.group(2) is not None and '$' in match.group(2):
            return f'{text!r}: a default holds no variable ({match.group(0)})'
    stray = _STRAY.search(VARIABLE.sub('', text))
    if stray is None:
        return None
    if stray.group(1) == '{':
        return f'{text!r}: a variable is ${{NAME}} or ${{NAME:-default}}, NAME a letter or _, then letters or digits'
    return f'{text!r}: write a variable as ${{{stray.group(1)}}}'


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
        path = _path(source.file, base, name, where)
        value = _read(path, name, where).strip()
        if not value:
            msg = f'inject {name!r}: {path} is empty'
            raise PolicyFileError(msg, path=where)
        return Injected(name, describe(source), value, str(path))
    if source.toml is None:  # the policy's checks make a source with no env or file a whole toml one
        msg = f'inject {name!r}: a source is env, file, or toml with its key'
        raise PolicyFileError(msg, path=where)
    path = _path(source.toml, base, name, where)
    return Injected(name, describe(source), _from_toml(name, source, path, where), str(path))


def _path(text: str, base: Path, name: str, where: tuple[str, ...]) -> Path:
    """A source's path: its variables expanded, then ``~``; relative to ``base`` (the policy's directory)."""

    def value(match: re.Match[str]) -> str:
        variable, default = match.group(1), match.group(2)
        if os.environ.get(variable):
            return os.environ[variable]
        if default is not None:
            return default
        msg = f'inject {name!r}: ${{{variable}}} in {text!r} is not set, and has no default (${{{variable}:-…}})'
        raise PolicyFileError(msg, path=where)

    path = Path(VARIABLE.sub(value, text)).expanduser()
    return path if path.is_absolute() else base / path


def _read(path: Path, name: str, where: tuple[str, ...]) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError as exc:
        msg = f'inject {name!r}: could not read {path}: {exc.strerror or exc}'
        raise PolicyFileError(msg, path=where) from exc


def _from_toml(name: str, source: Source, path: Path, where: tuple[str, ...]) -> str:
    key = source.key
    if key is None:  # the policy's checks give every toml source its key
        msg = f'inject {name!r}: a toml source names its key'
        raise PolicyFileError(msg, path=where)
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
