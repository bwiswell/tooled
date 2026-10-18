"""Output: how a result reaches stdout, and what keeps it to a size an agent can read.

- **Dumps** are seared's JSON form, except a ``Bytes`` field, which shows its length and its SHA-256 rather than
  its base64 (``{"bytes": n, "sha256": …}``). Fields nested in other classes are reached too; a union's are not.
- **``--select PATHS``** keeps parts of a result, with its structure: comma-separated paths, dotted
  (``commander.name``), with ``[]`` mapping over a list (``systems[].key``). Applied to each item of a list result,
  or to the whole of any other. A path that matches nothing in a non-empty result is refused, so a typo shows.
- **The cap.** A result over ``--max-bytes`` (else ``$PARSED_MAX_BYTES``, else 28,000 bytes) isn't printed: the
  error says its size, the size of each part, and how to get it. 28,000 is just under the 30,000 characters
  Claude Code shows of a command's output inline; past those, it keeps the output in a file and shows only its
  start, which is worse than a refusal that says what's there. ``0`` turns the cap off.
- **``--out FILE``** writes the whole result there, uncapped, and prints where and how big.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seared import FieldKind
from seared.doc import introspect, is_seared_class

from .errors import TooLargeError, UsageError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from seared import Seared

#: The cap's default: just under the 30,000 characters Claude Code shows of a command's output inline.
DEFAULT_MAX_BYTES = 28_000

#: The environment variable that sets the cap, when ``--max-bytes`` doesn't.
MAX_BYTES_ENV = 'PARSED_MAX_BYTES'

#: How many parts a too-large error names, the biggest first.
_PARTS = 12

type Segment = tuple[str, bool]  # a key, and whether ``[]`` maps over the list there
type Path_ = list[Segment]


# ---- dumps -------------------------------------------------------------------------------------


def dump(cls: type[Seared], item: Any) -> Any:  # noqa: ANN401 - seared's JSON form
    """``item`` in seared's JSON form, with its ``Bytes`` fields as their length and SHA-256."""
    return summarize_bytes(cls, cls.dump(item))


def summarize_bytes(cls: type, data: Any) -> Any:  # noqa: ANN401 - seared's JSON form
    """``data`` (a dump of ``cls``) with each ``Bytes`` field's base64 replaced by its length and SHA-256."""
    if not is_seared_class(cls) or not isinstance(data, dict):
        return data
    for field in introspect(cls).fields:
        key = field.wire_key or field.attr
        if key not in data or data[key] is None:
            continue
        if field.kind is FieldKind.BYTES:
            data[key] = _each(data[key], field, _digest)
        elif field.nested is not None:
            nested = field.nested
            data[key] = _each(data[key], field, lambda value, nested=nested: summarize_bytes(nested, value))
    return data


def _each(value: Any, field: Any, apply: Any) -> Any:  # noqa: ANN401 - a dumped field, and what to do to it
    if field.keyed and isinstance(value, dict):
        return {k: apply(v) for k, v in value.items()}
    if field.many and isinstance(value, list):
        return [apply(v) for v in value]
    return apply(value)


def _digest(text: Any) -> Any:  # noqa: ANN401 - a base64 string, as seared dumps bytes
    try:
        raw = base64.b64decode(text, validate=True)
    except binascii.Error, TypeError, ValueError:
        return text
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


# ---- select ------------------------------------------------------------------------------------


def parse_paths(text: str) -> list[Path_]:
    """``--select``'s paths: comma-separated, dotted, with ``[]`` mapping over a list.

    Raises:
        UsageError: A path with an empty part, or brackets anywhere but closing a part.
    """
    paths: list[Path_] = []
    for raw in text.split(','):
        path: Path_ = []
        for part in raw.strip().split('.'):
            each = part.endswith('[]')
            name = part.removesuffix('[]')
            if not name or any(c in name for c in '[]'):
                msg = f'--select takes dotted paths, [] mapping over a list (systems[].key), not {raw!r}'
                raise UsageError(msg)
            path.append((name, each))
        paths.append(path)
    return paths


def select(value: Any, paths: Sequence[Path_]) -> Any:  # noqa: ANN401 - a JSON-ready result
    """The parts of ``value`` the paths name, structure kept: of each item, when ``value`` is a list.

    Raises:
        UsageError: A path matches nothing, though there was something to match.
    """
    picked, matched = pick(value, paths)
    if value != []:
        for path, found in zip(paths, matched, strict=True):
            if not found:
                msg = f'--select {path_text(path)!r} matches nothing in the result'
                raise UsageError(msg)
    return picked


def pick(value: Any, paths: Sequence[Path_]) -> tuple[Any, list[bool]]:  # noqa: ANN401 - a JSON-ready result
    """:func:`select`, refusing nothing: the parts, and whether each path matched anything."""
    items = value if isinstance(value, list) else [value]
    matched = [False] * len(paths)
    picked = []
    for item in items:
        out: Any = {}
        for index, path in enumerate(paths):
            found, part = _project(item, path)
            if found:
                matched[index] = True
                out = _merge(out, part)
        picked.append(out)
    return (picked if isinstance(value, list) else picked[0]), matched


def path_text(path: Path_) -> str:
    """A parsed path, written as it was given."""
    return '.'.join(name + ('[]' if each else '') for name, each in path)


def _project(value: Any, path: Path_) -> tuple[bool, Any]:  # noqa: ANN401 - a JSON-ready value
    if not path:
        return True, value
    (name, each), rest = path[0], path[1:]
    if not isinstance(value, dict) or name not in value:
        return False, None
    inner = value[name]
    if not each:
        found, part = _project(inner, rest)
        return (True, {name: part}) if found else (False, None)
    if not isinstance(inner, list):
        return False, None
    parts = [_project(element, rest) for element in inner]
    if inner and not any(found for found, _ in parts):
        return False, None
    return True, {name: [part if found else {} for found, part in parts]}


def _merge(a: Any, b: Any) -> Any:  # noqa: ANN401 - JSON-ready values
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for key, value in b.items():
            out[key] = _merge(out[key], value) if key in out else value
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [_merge(x, y) for x, y in zip(a, b, strict=True)]
    return b


# ---- the cap, and --out ------------------------------------------------------------------------


def max_bytes(given: int | None) -> int:
    """The cap: ``--max-bytes``, else ``$PARSED_MAX_BYTES``, else the default. ``0`` means none.

    Raises:
        UsageError: The variable isn't a whole number of bytes, zero or more.
    """
    if given is not None:
        return given
    text = os.environ.get(MAX_BYTES_ENV)
    if not text:
        return DEFAULT_MAX_BYTES
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value < 0:
        msg = f'${MAX_BYTES_ENV} is a whole number of bytes, zero or more, not {text!r}'
        raise UsageError(msg)
    return value


def render(value: Any, *, indent: int | None) -> str:  # noqa: ANN401 - a JSON-ready result
    """``value`` as the JSON text that would be printed."""
    return json.dumps(value, indent=indent, ensure_ascii=False)


def check_size(text: str, value: Any, *, cap: int) -> None:  # noqa: ANN401 - the result the text renders
    """Refuse ``text`` (rendering ``value``) when it's over ``cap`` bytes.

    Raises:
        TooLargeError: It is, with its size, each part's size, and how to get it.
    """
    size = len(text.encode())
    if cap and size > cap:
        msg = (
            f'the result is {size:,} bytes, over the {cap:,}-byte cap: --select keeps parts of it, --out writes it '
            'whole to a file, --max-bytes moves the cap (0 for none)'
        )
        raise TooLargeError(msg, size=size, cap=cap, parts=parts(value))


def parts(value: Any) -> dict[str, Any]:  # noqa: ANN401 - a JSON-ready result
    """The size of each part of ``value``, the biggest first: its keys, or its items' keys, summed."""
    sizes: dict[str, int] = {}
    if isinstance(value, dict):
        sizes = {key: len(render(part, indent=None)) for key, part in value.items()}
    elif isinstance(value, list):
        for item in value:
            for key, part in item.items() if isinstance(item, dict) else ():
                sizes[key] = sizes.get(key, 0) + len(render(part, indent=None))
    biggest = sorted(sizes.items(), key=lambda kv: -kv[1])[:_PARTS]
    out: dict[str, Any] = {'items': len(value)} if isinstance(value, list) else {}
    out['bytes'] = dict(biggest)
    return out


def write_out(path: str, text: str) -> dict[str, Any]:
    """Write ``text`` to ``path``, whole; what the command prints instead.

    Raises:
        UsageError: It couldn't be written.
    """
    target = Path(path).expanduser()
    try:
        target.write_text(text + '\n', encoding='utf-8')
    except OSError as exc:
        msg = f'could not write {target}: {exc.strerror or exc}'
        raise UsageError(msg) from exc
    return {'written': str(target.resolve()), 'bytes': len(text.encode()) + 1}
