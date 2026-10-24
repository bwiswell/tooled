"""Calls: one tool call, as a model wrote it, mapped back to the call of the verb its tool names.

``tooled call TOOL --args JSON`` carries out a tool ``tooled tools`` exported (:mod:`tooled.tools`). Its arguments
are the verb's own, by other names:

- **a slot's** value is the verb's ``slot=value``: a string as it is, a number or a boolean as JSON writes it;
- **a query's or a write's** other arguments are its request, the tool's variant's tag added under the union's tag
  key;
- **a wait's** ``until`` is its conditions, each a top-level field and the value it must equal, and its ``timeout``
  its bound;
- **a listen's** ``count`` and ``timeout`` say when it stops.

**What's checked here** is what the tool says and the verb can't: every required parameter given, none it doesn't
take, a slot one value of its type, a condition on a field the wait can compare and a value of that field's type
(one of its values, when it lists them), and a count and a timeout in range. A request's fields are left to the
verb: the policy refuses a field it injects or hides, as it would from ``--request``, and seared refuses the rest.

**Errors are worded for the model,** since a harness hands them straight back: the parameter at fault (in
``path``), what it takes, and the values allowed. They're ``validation`` errors, exit 4, as a request that doesn't
load is.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .catalog import top_fields
from .errors import InvalidRequestError
from .executor import Condition
from .schemas import tag_keys
from .tools import TIMEOUT

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .policy import Rules
    from .tools import Tool

#: The JSON types a value has, as a schema's ``type`` names them.
_TYPES: dict[type, str] = {
    bool: 'boolean',
    int: 'integer',
    float: 'number',
    str: 'string',
    list: 'array',
    dict: 'object',
}


@dataclass(frozen=True, slots=True)
class Arguments:
    """A tool call's arguments, as its verb takes them.

    Attributes:
        slots: Each slot given, as ``slot=value`` gives it.
        request: A query's or a write's request, in its wire form; ``None`` for any other verb.
        until: A wait's conditions.
        count: How many values a listen gives.
        timeout: A wait's or a listen's bound, in seconds; ``None`` for the verbs that bound themselves.
    """

    slots: dict[str, str] = field(default_factory=dict)
    request: dict[str, Any] | None = None
    until: tuple[Condition, ...] = ()
    count: int | None = None
    timeout: float | None = None


def arguments(tool: Tool, given: Mapping[str, Any], rules: Rules) -> Arguments:
    """``given``, a call's arguments, as ``tool``'s verb takes them.

    Args:
        tool: The tool called.
        given: The arguments the model wrote: a JSON object.
        rules: The policy's rules, which bind slots.

    Raises:
        InvalidRequestError: A required parameter missing, one the tool doesn't take, or a value it can't take;
            ``path`` names it.
    """
    params: dict[str, Any] = tool.parameters['properties']
    missing = [name for name in tool.parameters.get('required', []) if name not in given]
    if missing:
        msg = f'{tool.name} needs {_listed(missing)}'
        raise InvalidRequestError(msg, path=(missing[0],))
    slotted = set(tool.contract.slot_names)
    bound = rules.bound(tool.contract)
    taken = _taken(tool) if tool.verb in {'query', 'invoke'} else set()
    slots, rest = {}, {}
    for name, value in given.items():
        if name in slotted and name in params:
            slots[name] = _slot(tool, name, value, params[name])
        elif name in params or name in taken:
            rest[name] = value
        elif name in bound:  # a bound slot given its own value passes; another, the policy refuses
            slots[name] = _slot(tool, name, value, {})
        else:
            msg = f'{tool.name} takes no {name!r}; it takes {_listed(list(params)) or "none"}'
            raise InvalidRequestError(msg, path=(name,))
    if tool.verb == 'wait':
        conditions = _conditions(tool, rest['until'], params['until'])
        return Arguments(slots, until=conditions, timeout=_seconds(rest.get('timeout', TIMEOUT)))
    if tool.verb == 'listen':
        return Arguments(slots, count=_count(rest.get('count', 1)), timeout=_seconds(rest.get('timeout', TIMEOUT)))
    if tool.verb in {'query', 'invoke'}:
        return Arguments(slots, request=_request(tool, rest))
    return Arguments(slots)


def _taken(tool: Tool) -> set[str]:
    """The fields at a query's or a write's request's root, its variant's included, and its union's tag key.

    Beyond the tool's parameters, they're what the policy injects or hides, which it refuses with its own reasons,
    and the tag, which the tool fixes.
    """
    request = tool.contract.request
    if request is None:
        return set()
    fields = top_fields(request)
    own = set(fields.get(None, {}))
    variant = set(fields.get(tool.variant, {})) if tool.variant is not None else set()
    return own | variant | set(tag_keys(request))


def _request(tool: Tool, given: dict[str, Any]) -> dict[str, Any]:
    """A query's or a write's request: the arguments beyond the slots, and the tool's variant's tag."""
    request = tool.contract.request
    if tool.variant is None or request is None:
        return given
    key = tag_keys(request)[0]
    if key in given and given[key] != tool.variant:
        msg = f'{tool.name} is variant {tool.variant!r}, so its {key!r} is {tool.variant!r}; leave it out'
        raise InvalidRequestError(msg, path=(key,))
    return {**given, key: tool.variant}


def _slot(tool: Tool, name: str, value: Any, schema: Mapping[str, Any]) -> str:
    """A slot's value as ``slot=value`` gives it: a string as it is; a number or a boolean as JSON writes it."""
    _check(tool, (name,), value, schema)
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (list, dict)):
        msg = f'{tool.name}: {name!r} is one value, not {_kind(value)}'
        raise InvalidRequestError(msg, path=(name,))
    return json.dumps(value)


def _conditions(tool: Tool, given: Any, schema: Mapping[str, Any]) -> tuple[Condition, ...]:
    """A wait's ``until``: each field it names, and the value that field must equal."""
    fields: dict[str, Any] = schema.get('properties', {})
    if not isinstance(given, dict) or not given:
        shape = 'empty' if isinstance(given, dict) else _kind(given)
        msg = f"{tool.name}: 'until' is an object of one condition or more, each a field and the value it must "
        msg += f'equal ({_example(fields)}), not {shape}'
        raise InvalidRequestError(msg, path=('until',))
    out = []
    for name, value in given.items():
        if name not in fields:
            msg = f"{tool.name} can't wait on {name!r}; 'until' takes {_listed(list(fields)) or 'none'}"
            raise InvalidRequestError(msg, path=('until', name))
        _check(tool, ('until', name), value, fields[name])
        out.append(Condition((name,), value, f'{name}={value if isinstance(value, str) else json.dumps(value)}'))
    return tuple(out)


def _seconds(value: Any) -> float:
    """A wait's or a listen's ``timeout``: a number of seconds above zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        msg = f"'timeout' is a number of seconds above zero, not {json.dumps(value)}"
        raise InvalidRequestError(msg, path=('timeout',))
    return float(value)


def _count(value: Any) -> int:
    """A listen's ``count``: a whole number above zero."""
    whole = isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if isinstance(value, bool) or not whole or value < 1:
        msg = f"'count' is a whole number above zero, not {json.dumps(value)}"
        raise InvalidRequestError(msg, path=('count',))
    return int(value)


def _check(tool: Tool, path: Sequence[str], value: Any, schema: Mapping[str, Any]) -> None:
    """``value`` against a one-value schema: its type, and its enum or const when it has one.

    Raises:
        InvalidRequestError: It's of another type, or none of the values allowed.
    """
    where = '.'.join(path)
    allowed = schema.get('enum', [schema['const']] if 'const' in schema else None)
    if allowed is not None and value not in allowed:
        msg = f'{tool.name}: {where!r} is one of {", ".join(map(json.dumps, allowed))}; not {json.dumps(value)}'
        raise InvalidRequestError(msg, path=tuple(path))
    kinds = schema.get('type')
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
    if kinds and not any(_is(value, kind) for kind in kinds):
        msg = f'{tool.name}: {where!r} is {" or ".join(map(_article, kinds))}, not {_kind(value)}'
        raise InvalidRequestError(msg, path=tuple(path))


def _is(value: Any, kind: str) -> bool:
    """Whether ``value`` is of the JSON type ``kind``: an integer is a number too, and so is a whole float."""
    if kind == 'null':
        return value is None
    if kind == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == 'integer':
        return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer()
    return _TYPES.get(type(value)) == kind


def _kind(value: Any) -> str:
    """What ``value`` is, for a message: ``a string``, ``null``."""
    if value is None:
        return 'null'
    return _article(_TYPES.get(type(value), type(value).__name__))


def _article(kind: str) -> str:
    return f'an {kind}' if kind[:1] in 'aeiou' else f'a {kind}'


def _example(fields: Mapping[str, Any]) -> str:
    """An ``until`` the model could write, from the first field it takes."""
    if not fields:
        return '{}'
    name, schema = next(iter(fields.items()))
    sample = {'string': 'value', 'integer': 1, 'number': 1, 'boolean': True}
    value = (schema.get('enum') or [schema.get('const')])[0] if ('enum' in schema or 'const' in schema) else None
    kind = schema.get('type')
    if value is None:
        value = sample.get(kind if isinstance(kind, str) else (kind or ['string'])[0], 'value')
    return json.dumps({name: value})


def _listed(names: Sequence[str]) -> str:
    """``'a'``, ``'a' and 'b'``, ``'a', 'b' and 'c'``."""
    *rest, last = [repr(name) for name in names] or ['']
    return f'{", ".join(rest)} and {last}' if rest else last
