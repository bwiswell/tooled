"""Tools: the operations a policy shows, as tool definitions a model's harness can hand it.

``tooled tools`` prints them; a harness offers them to a model, and a tool call the model makes is carried out as
the operation it names. Each tool is one call of one verb:

- **``fetch``** a retained contract: its slots, each optional (left out, every value);
- **``query``** a queryable that declares ``EFFECT = READ``: its slots, each optional, and its request;
- **``invoke``** a queryable that writes: its slots, each required (a write reaches one key), and its request;
- **``wait``** on a retained contract or a stream: its slots, each optional; ``until``, its conditions, one typed
  property for each of the reply's top-level fields that holds a single value (a field a bound slot fixes left
  out), at least one given; and ``timeout``, in seconds;
- **``listen``** to a stream: its slots, each optional; ``count``, the values to give; and ``timeout``. A retained
  contract's listen would give what its fetch does, so it has none.

A request with a union at its root becomes **one tool per variant** the policy allows, the variant's tag fixed by
the tool, so each tool is flat: the request's own fields beside the variant's, or the variant's under its payload
key when the union nests it. A presence feed has no verb yet, so no tool.

**Parameters** are the unbound slots, then the request's fields as the protocol writes them on the wire, without
the fields the policy injects or hides and without the union's tag, or a wait's or a listen's own. A slot that
shares its name with another parameter is refused, since one parameter can't be both: binding the slot settles it.

**The dialect** is the subset of JSON Schema a constrained decoder holds (llama.cpp's grammar converter, say):
references inlined; every object with ``properties`` closed by ``additionalProperties: false``; required
properties first; no ``$schema``, ``title`` or ``$defs``. A field whose schema still holds a union (``allOf``,
``anyOf``, ``oneOf``, ``if``/``then``, ``not``) once its references are inlined can't be said in it, so the export
is refused, naming the field, and the field at the request's root a policy could hide to leave it out. seared
still checks every call; the schema only helps the model write one.

**Descriptions** are a docstring's first line: a variant's, for a variant's tool; a write's request's; else the
contract's, which says what a read gives; a wait's and a listen's then say what they do.

**Names** are the verb, the class's name in snake case and the variant's tag (``invoke_order_result_mine``), each
run of other characters one ``_``; a class whose bare name is ambiguous is named by ``module:Class``. A name must
fit ``[A-Za-z0-9_-]{1,64}`` and be the only one of its kind, or the export is refused.

**Byte-stable:** the same policy and protocol print the same bytes, tools sorted by name, so a model server's
prompt cache keeps hitting.

**Found by name:** :func:`find` builds the one tool a call names, as :func:`tools` would, and no other, so a call
needs only its own tool to be one the dialect can say.
"""

from __future__ import annotations

import copy
import difflib
import fnmatch
import itertools
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

from seared.doc import json_schema

from .catalog import Kind
from .errors import PolicyFileError, UsageError
from .output import as_json
from .schemas import branch_tag, request_schema, slot_schemas, summary_line, variant_classes, verbs

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from .catalog import Contract
    from .policy import Rules

#: The forms a tool is written in: OpenAI's chat completions, Anthropic's Messages, and MCP's ``tools/list``.
FORMATS = ('openai', 'anthropic', 'mcp')

#: The verbs a tool carries out.
CALLS = ('fetch', 'query', 'invoke', 'wait', 'listen')

#: A wait's and a listen's bound, in seconds, when a call gives none: ``tooled wait``'s own default.
TIMEOUT = 30

#: The characters that make a pattern a glob.
_GLOB = frozenset('*?[')

#: The JSON types a condition can compare: a field that holds one value.
_SCALARS = frozenset({'string', 'integer', 'number', 'boolean', 'null'})

#: What a tool's name must fit (OpenAI's and Anthropic's rule alike).
NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')

#: Keywords that make a union, which the dialect can't hold.
_UNIONS = ('allOf', 'anyOf', 'oneOf', 'if', 'then', 'else', 'not')

#: Keywords the dialect leaves out.
_DROPPED = frozenset({'$schema', 'title', '$defs', 'unevaluatedProperties'})


@dataclass(frozen=True, slots=True)
class Tool:
    """One tool: a call on one contract, for one variant of its request's union when it has one.

    Attributes:
        name: The tool's name.
        description: What it does: a variant's summary; a write's request's; else the contract's, what a read gives.
        parameters: Its parameters' schema, in the dialect.
        contract: The contract it calls.
        verb: ``fetch``, ``query``, ``invoke``, ``wait`` or ``listen``.
        variant: The tag it fixes, or ``None``.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    contract: Contract
    verb: str
    variant: str | None


def tools(rules: Rules, picks: Sequence[str] = CALLS) -> list[Tool]:
    """Every tool the policy shows that ``picks`` keeps, sorted by name.

    Args:
        rules: The policy's rules over its catalog.
        picks: Each a verb of :data:`CALLS`, for all of its tools, or ``verb:GLOB``, for those whose operation's
            name or ``module:Class`` the glob matches (``wait:Fleet*``); every verb's tools by default.

    Raises:
        UsageError: A pick names no verb, or a pattern with no glob names no operation.
        PolicyFileError: A shown operation can't be a tool: fields the dialect can't say (every one named, once
            each), a slot that's also a request field, two unions at its request's root, or a name that doesn't fit
            or comes twice.
    """
    found: list[Tool] = []
    unsayable: dict[tuple[str, str], tuple[str, str | None]] = {}
    kept = _picked(picks, rules)
    for contract, verb, tag in _each(rules):
        if not _keeps(kept.get(verb, ()), contract):
            continue
        try:
            found.append(_tool(contract, rules, verb, tag))
        except _Unsayable as exc:
            unsayable.setdefault((exc.where, exc.path), (exc.why, exc.field))
    if unsayable:
        raise PolicyFileError(_unsayable_message(unsayable))
    found.sort(key=lambda tool: tool.name)
    for first, second in itertools.pairwise(found):
        if first.name == second.name:
            msg = f'two tools would be named {first.name!r}: {_what(first, rules)} and {_what(second, rules)}'
            raise PolicyFileError(msg)
    return found


def find(rules: Rules, name: str) -> Tool:
    """The tool named ``name`` among those the policy shows, built alone.

    Raises:
        UsageError: No tool the policy shows has that name; the closest are suggested.
        PolicyFileError: It can't be a tool, as :func:`tools` would refuse it: fields the dialect can't say, a
            slot that's also a request field, or another tool of the same name.
    """
    label = rules.catalog.label
    named = [(c, verb, tag) for c, verb, tag in _each(rules) if _name(verb, label(c), tag) == name]
    if not named:
        names = sorted(_name(verb, label(c), tag) for c, verb, tag in _each(rules))
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
        hint = f'; did you mean {" or ".join(map(repr, close))}?' if close else ''
        msg = f'no tool named {name!r} among those the policy shows{hint}'
        raise UsageError(msg)
    found = []
    for contract, verb, tag in named:
        try:
            found.append(_tool(contract, rules, verb, tag))
        except _Unsayable as exc:
            raise PolicyFileError(_unsayable_message({(exc.where, exc.path): (exc.why, exc.field)})) from None
    if len(found) > 1:
        msg = f'two tools would be named {name!r}: {_what(found[0], rules)} and {_what(found[1], rules)}'
        raise PolicyFileError(msg)
    return found[0]


def _each(rules: Rules) -> Iterator[tuple[Contract, str, str | None]]:
    """Each call a tool could make: a contract the policy shows, a verb, and an allowed variant's tag or ``None``."""
    for contract in rules.visible_contracts():
        for verb in calls(contract):
            asked = verb in {'query', 'invoke'} and contract.request is not None and contract.variants
            for tag in rules.allowed_variants(contract) if asked else [None]:
                yield contract, verb, tag


def _picked(picks: Sequence[str], rules: Rules) -> dict[str, list[str]]:
    """Each verb ``picks`` keeps, with its globs; a bare verb's is ``*``.

    Raises:
        UsageError: A pick names no verb, or a pattern with no glob names no operation.
    """
    kept: dict[str, list[str]] = {}
    for pick in picks:
        verb, _, pattern = pick.partition(':')
        if verb not in CALLS:
            msg = f'a tool verb is one of {", ".join(CALLS)}, not {verb!r}'
            raise UsageError(msg)
        if pattern and not _GLOB & set(pattern):
            rules.catalog.get(pattern)  # an exact name names an operation, as a rule's must
        kept.setdefault(verb, []).append(pattern or '*')
    return kept


def _keeps(globs: Sequence[str], contract: Contract) -> bool:
    """Whether a glob of ``globs`` matches ``contract``'s name or ``module:Class``."""
    return any(fnmatch.fnmatchcase(contract.name, g) or fnmatch.fnmatchcase(contract.qualname, g) for g in globs)


def _unsayable_message(unsayable: dict[tuple[str, str], tuple[str, str | None]]) -> str:
    """The refusal for the fields a tool's schema can't say: each where it is, why, and what leaves it out.

    An optional field at the request's root can be hidden; anything else, its operation or variant denied.
    """
    if len(unsayable) == 1:
        (((where, path), (why, field)),) = unsayable.items()
        fix = f'hide {field!r} in the policy, or deny {where}' if field else f'deny {where} in the policy'
        return f"{where}: field {path!r} is {why}, which a tool's schema can't say; {fix}"
    listed = ', '.join(
        f'{where} {path!r} ({why}; ' + (f'hide {field!r})' if field else f'deny {where})')
        for (where, path), (why, field) in unsayable.items()
    )
    return f"{len(unsayable)} fields can't be said in a tool's schema; hide or deny them in the policy: {listed}"


def calls(contract: Contract) -> list[str]:
    """The verbs a tool can carry out on ``contract``: its verbs, but a retained contract's listen."""
    return [
        verb for verb in verbs(contract) if verb in CALLS and not (verb == 'listen' and contract.kind is Kind.RETAINED)
    ]


def untooled(rules: Rules) -> list[Contract]:
    """The contracts the policy shows that no tool calls: presence feeds, which no verb acts on yet."""
    return [contract for contract in rules.visible_contracts() if not calls(contract)]


def spec(tool: Tool, form: str) -> dict[str, Any]:
    """``tool`` written in ``form``, one of :data:`FORMATS`."""
    if form == 'anthropic':
        return {'name': tool.name, 'description': tool.description, 'input_schema': tool.parameters}
    if form == 'mcp':
        return {'name': tool.name, 'description': tool.description, 'inputSchema': tool.parameters}
    return {
        'type': 'function',
        'function': {'name': tool.name, 'description': tool.description, 'parameters': tool.parameters},
    }


def sizes(found: list[Tool], form: str) -> dict[str, Any]:
    """Each tool's size, written in ``form`` as ``tools`` prints it, in bytes and estimated tokens (bytes / 4)."""
    rows: list[dict[str, Any]] = []
    for tool in found:
        size = len(as_json(spec(tool, form), indent=None).encode())
        rows.append({'name': tool.name, 'bytes': size, 'tokens': math.ceil(size / 4)})
    return {
        'format': form,
        'tools': rows,
        'total': {
            'tools': len(rows),
            'bytes': sum(row['bytes'] for row in rows),
            'tokens': sum(row['tokens'] for row in rows),
        },
    }


# ---- one tool ----------------------------------------------------------------------------------


def _tool(contract: Contract, rules: Rules, verb: str, tag: str | None) -> Tool:
    """The tool for ``verb`` on ``contract``, for the variant ``tag`` when its request has a union."""
    label = rules.catalog.label(contract)
    name = _name(verb, label, tag)
    if not NAME.fullmatch(name):
        msg = f'{label}: its tool name, {name!r}, is longer than 64 characters'
        raise PolicyFileError(msg)
    described = dict(variant_classes(contract))[tag] if tag is not None else None
    if described is None and verb == 'invoke':
        described = contract.request
    description = next(
        (line for cls in (described, contract.cls) if cls is not None and (line := summary_line(cls))), label
    )
    description += _DOES.get(verb, '')
    return Tool(name, description, _parameters(contract, rules, verb, tag, label), contract, verb, tag)


#: What a wait and a listen do, after the contract's docstring says what it holds.
_DOES = {
    'wait': ' Waits for a value that meets every condition in `until`, up to `timeout` seconds, and gives it.',
    'listen': ' Gives the next `count` values as they come, up to `timeout` seconds.',
}


def _parameters(contract: Contract, rules: Rules, verb: str, tag: str | None, label: str) -> dict[str, Any]:
    """A tool's parameters, in the dialect: the unbound slots, then the rest (:func:`_rest`)."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for slot, schema in slot_schemas(contract, rules).items():
        if 'bound' not in schema:
            properties[slot] = _Dialect(schema.pop('$defs', {}), label).portable(schema, slot)
            required += [slot] if verb == 'invoke' else []
    for where, props, needed in _rest(contract, rules, verb, tag, label):
        for name, prop in props.items():
            if name in properties:
                msg = f"{where}: {name!r} is both a slot and another parameter, and one parameter can't be both;"
                msg += ' bind the slot, or deny the operation'
                raise PolicyFileError(msg)
            properties[name] = prop
        required += [name for name in needed if name not in required]
    parameters: dict[str, Any] = {'type': 'object', 'properties': _required_first(properties, required)}
    if required:
        parameters['required'] = [name for name in parameters['properties'] if name in required]
    parameters['additionalProperties'] = False
    return parameters


def _rest(
    contract: Contract, rules: Rules, verb: str, tag: str | None, label: str
) -> list[tuple[str, dict[str, Any], list[str]]]:
    """A tool's parameters beside its slots, in parts, each with where it's from, its properties, and the required.

    A query's or a write's are its request's fields, then ``tag``'s: a field of the request's own is named by the
    operation alone, and a variant's by the operation and its tag, so a field every variant shares is named once. A
    wait's are its conditions and its bound; a listen's its count and its bound.
    """
    bound = {'type': 'number', 'description': 'how long at most, in seconds', 'default': TIMEOUT, 'exclusiveMinimum': 0}
    if verb == 'wait':
        return [(label, {'until': _conditions(contract, rules, label), 'timeout': bound}, ['until'])]
    if verb == 'listen':
        count = {'type': 'integer', 'description': 'how many values to give', 'default': 1, 'minimum': 1}
        return [(label, {'count': count, 'timeout': bound}, [])]
    request = request_schema(contract, rules, tag) if verb != 'fetch' else None
    if request is None:
        return []
    defs = request.get('$defs', {})
    parts = [(label, request.get('properties', {}), request.get('required', []))]
    if tag is not None:
        props, needed = _branch(request, tag, label)
        hidden = rules.hidden(contract, tag)
        parts.append((f'{label} variant {tag!r}', {n: p for n, p in props.items() if n not in hidden}, needed))
    return [(where, _said(_Dialect(defs, where), props, needed), needed) for where, props, needed in parts]


def _said(dialect: _Dialect, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Request fields in the dialect, each by its wire name; a field it can't say names itself, if it's optional."""
    out = {}
    for name, prop in props.items():
        try:
            out[name] = dialect.portable(prop, name)
        except _Unsayable as exc:
            exc.field = None if name in required else name
            raise
    return out


def _conditions(contract: Contract, rules: Rules, label: str) -> dict[str, Any]:
    """A wait's ``until``: a property for each of the reply's top-level fields a condition can compare.

    Each is the field's dump schema in the dialect, by its wire name. A field that holds more than one value (an
    object, a list, a map) or that the dialect can't say has none, and nor does a field a bound slot fixes.
    """
    schema = json_schema(contract.cls, mode='dump')
    dialect, bound = _Dialect(schema.get('$defs', {}), label), rules.bound(contract)
    fields: dict[str, Any] = {}
    for name, prop in schema.get('properties', {}).items():
        try:
            said = dialect.portable(prop, f'until.{name}')
        except _Unsayable:
            continue
        if name not in bound and _single(said):
            fields[name] = said
    return {
        'type': 'object',
        'description': "conditions on the value's fields, each the value a field must equal; every one must hold",
        'properties': fields,
        'minProperties': 1,
        'additionalProperties': False,
    }


def _single(schema: dict[str, Any]) -> bool:
    """Whether ``schema`` holds one value a condition can compare: a scalar type, an enum, or a const."""
    kind = schema.get('type')
    kinds = set(kind) if isinstance(kind, list) else {kind} if isinstance(kind, str) else set()
    return (bool(kinds) and kinds <= _SCALARS) or 'enum' in schema or 'const' in schema


def _branch(request: dict[str, Any], tag: str, where: str) -> tuple[dict[str, Any], list[str]]:
    """The properties and required names ``tag``'s branch adds to the request's root, as seared writes them.

    A flat union's branch is the variant's class itself; a nested one's puts the class under its payload key.
    """
    unions = [envelope for envelope in request.get('allOf', ()) if isinstance(envelope.get('allOf'), list)]
    if len(unions) > 1:
        msg = f'{where}: its request has {len(unions)} unions at its root, and a tool fixes one tag'
        raise PolicyFileError(msg)
    defs = request.get('$defs', {})
    for branch in unions[0]['allOf'] if unions else ():
        if branch_tag(branch)[1] == tag:
            then = branch.get('then', {})
            ref = then.get('$ref', '').removeprefix('#/$defs/')
            body = copy.deepcopy(defs[ref]) if ref in defs else then
            return body.get('properties', {}), body.get('required', [])
    return {}, []


@dataclass(frozen=True, slots=True)
class _Dialect:
    """Rewrites a schema in the dialect.

    Attributes:
        defs: The definitions its references name.
        where: The operation it's for, to name in a refusal.
    """

    defs: dict[str, Any]
    where: str

    def portable(self, node: Any, path: str, seen: tuple[str, ...] = ()) -> Any:  # a JSON Schema node
        """``node`` in the dialect: references inlined, objects closed, required first, no unions.

        Raises:
            PolicyFileError: It holds a union, or a reference to itself, which the dialect can't say.
        """
        if not isinstance(node, dict):
            return node
        ref = node.get('$ref')
        if isinstance(ref, str):
            name = ref.removeprefix('#/$defs/')
            if name in seen or name not in self.defs:
                self._refuse(path, 'a reference to itself')
            merged = {**copy.deepcopy(self.defs[name]), **{key: value for key, value in node.items() if key != '$ref'}}
            return self.portable(merged, path, (*seen, name))
        if any(key in node for key in _UNIONS):
            self._refuse(path, 'a union')
        out = {key: self._rewrite(key, value, node, path, seen) for key, value in node.items() if key not in _DROPPED}
        if 'properties' in out and 'additionalProperties' not in out:
            out['additionalProperties'] = False
        return out

    def _rewrite(self, key: str, value: Any, node: dict[str, Any], path: str, seen: tuple[str, ...]) -> Any:
        """One keyword's value in the dialect: the schemas it holds rewritten, the rest as it is."""
        if key == 'properties':
            props = {name: self.portable(prop, f'{path}.{name}', seen) for name, prop in value.items()}
            return _required_first(props, node.get('required', []))
        if key in {'items', 'additionalProperties'} and isinstance(value, dict):
            return self.portable(value, f'{path}[]', seen)
        if key == 'prefixItems' and isinstance(value, list):
            return [self.portable(item, f'{path}[{at}]', seen) for at, item in enumerate(value)]
        return value

    def _refuse(self, path: str, why: str) -> NoReturn:
        raise _Unsayable(self.where, path, why)


class _Unsayable(Exception):  # noqa: N818 -- not reported as it is: tools() gathers every one into one refusal
    """A field a tool's schema can't say: where it is, its path, why, and the optional root field it's in, if any."""

    def __init__(self, where: str, path: str, why: str) -> None:
        super().__init__(where, path, why)
        self.where, self.path, self.why = where, path, why
        self.field: str | None = None


def _required_first(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """``properties`` with the required ones first, each side in its own order."""
    return {name: properties[name] for name in sorted(properties, key=lambda name: name not in required)}


def _name(verb: str, label: str, tag: str | None) -> str:
    """A tool's name: the verb, the label in snake case, and the tag, each run of other characters one ``_``."""
    words = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', label)
    joined = '_'.join(part for part in (verb, words, tag) if part)
    return re.sub(r'[^a-z0-9]+', '_', joined.lower()).strip('_')


def _what(tool: Tool, rules: Rules) -> str:
    """What a tool calls, to name it in a refusal."""
    label = rules.catalog.label(tool.contract)
    return f'{tool.verb} {label}' + (f' variant {tool.variant!r}' if tool.variant is not None else '')
