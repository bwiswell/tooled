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

**On the tree,** each command the policy shows is a tool of its op's verb, ``see`` for a read, ``do`` for any other:
its parameters parsed's schema of its arguments (``parsed.doc.json_schema``), without the slots the policy binds,
each property's ``x-kind`` folded into its description, and the other ``x-`` keys left out. One that asks first
takes ``confirm``, answering yes where the policy allows it. **A chain is one tool,** its steps never their own:
``parsed.doc.chain_schema``, its shared fields and ``steps``, an array of the steps the policy shows, each a closed
object tagged by a ``const``. **An interactive command is never a tool,** whatever the policy says: it's for a person
at a terminal, and its ``INSTEAD`` is what a model says.

**Parameters** are the unbound slots, then the request's fields as the protocol writes them on the wire, without
the fields the policy injects or hides and without the union's tag, or a wait's or a listen's own. A slot that
shares its name with another parameter is refused, since one parameter can't be both: binding the slot settles it.

**The dialect** is the subset of JSON Schema a constrained decoder holds (llama.cpp's grammar converter, say):
references inlined; every object with ``properties`` closed by ``additionalProperties: false``; required
properties first; no ``$schema``, ``title``, ``$defs`` or ``x-`` keys. A field whose schema still holds a union
(``allOf``, ``oneOf``, ``if``/``then``, ``not``, or an ``anyOf`` whose branches aren't each a closed object tagged
by a ``const``) once its references are inlined can't be said in it, so the export is refused, naming the field,
and the field at the request's root a policy could hide to leave it out. A tagged ``anyOf``, a chain's steps', is
one a decoder holds. seared still checks every call; the schema only helps the model write one.

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

from parsed import Tree
from parsed.doc import chain_schema, introspect_chain
from parsed.doc import json_schema as command_schema
from seared.doc import json_schema

from .catalog import Command, Contract, Kind
from .errors import PolicyFileError, UsageError
from .output import as_json
from .policy import Op, op_of
from .schemas import branch_tag, request_schema, slot_schemas, summary_line, variant_classes, verbs

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from seared import Seared

    from .policy import Rules

#: The forms a tool is written in: OpenAI's chat completions, Anthropic's Messages, and MCP's ``tools/list``.
FORMATS = ('openai', 'anthropic', 'mcp')

#: The verbs a tool carries out: the wire's, then the tree's.
CALLS = ('fetch', 'query', 'invoke', 'wait', 'listen', 'see', 'do')

#: The tree's verbs among them.
TREE = frozenset({'see', 'do'})

#: A wait's and a listen's bound, in seconds, when a call gives none: ``tooled wait``'s own default.
TIMEOUT = 30

#: The characters that make a pattern a glob.
_GLOB = frozenset('*?[')

#: The JSON types a condition can compare: a field that holds one value.
_SCALARS = frozenset({'string', 'integer', 'number', 'boolean', 'null'})

#: What a tool's name must fit (OpenAI's and Anthropic's rule alike).
NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')

#: Keywords that make a union, which the dialect can't hold: but an ``anyOf`` of closed objects, each tagged.
_UNIONS = ('allOf', 'anyOf', 'oneOf', 'if', 'then', 'else', 'not')

#: The parameter a tool of a command that asks first takes, to answer it.
CONFIRM = {
    'type': 'boolean',
    'description': 'answer its question yes, where the policy allows; left out, the question comes back, nothing done',
    'default': False,
}

#: Keywords the dialect leaves out.
_DROPPED = frozenset({'$schema', 'title', '$defs', 'unevaluatedProperties'})


@dataclass(frozen=True, slots=True)
class Tool:
    """One tool: a call on one contract (for one variant of its request's union when it has one), or one command.

    Attributes:
        name: The tool's name.
        description: What it does: a variant's summary; a write's request's; else the contract's, what a read gives;
            a command's own.
        parameters: Its parameters' schema, in the dialect.
        operation: The contract it calls, or the command it runs; for a chain, its first step the policy shows.
        verb: ``fetch``, ``query``, ``invoke``, ``wait``, ``listen``, ``see`` or ``do``.
        variant: The tag it fixes, or ``None``.
        chain: For a chain's tool, the chain's base; ``None`` otherwise.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    operation: Contract | Command
    verb: str
    variant: str | None
    chain: type[Seared] | None = None


@dataclass(frozen=True, slots=True)
class _Call:
    """What a tool would call: an operation, a verb, a variant's tag, a chain's base."""

    operation: Contract | Command
    verb: str
    tag: str | None = None
    chain: type[Seared] | None = None

    def label(self, rules: Rules) -> str:
        """Its name, as its tool's name says it: the operation's label, or a chain's base, led by its prefix."""
        if self.chain is not None and isinstance(self.operation, Command):
            return '/'.join([*self.operation.under.split(), self.chain.__name__])
        return rules.catalog.label(self.operation)


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
    for call in _each(rules):
        if not _keeps(kept.get(call.verb, ()), call.operation):
            continue
        try:
            found.append(_tool(call, rules))
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
    named = [call for call in _each(rules) if _name(call.verb, call.label(rules), call.tag) == name]
    if not named:
        names = sorted(_name(call.verb, call.label(rules), call.tag) for call in _each(rules))
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
        hint = f'; did you mean {" or ".join(map(repr, close))}?' if close else ''
        msg = f'no tool named {name!r} among those the policy shows{hint}'
        raise UsageError(msg)
    found = []
    for call in named:
        try:
            found.append(_tool(call, rules))
        except _Unsayable as exc:
            raise PolicyFileError(_unsayable_message({(exc.where, exc.path): (exc.why, exc.field)})) from None
    if len(found) > 1:
        msg = f'two tools would be named {name!r}: {_what(found[0], rules)} and {_what(found[1], rules)}'
        raise PolicyFileError(msg)
    return found[0]


def _each(rules: Rules) -> Iterator[_Call]:
    """Each call a tool could make, of what the policy shows.

    A contract, with each verb and each allowed variant's tag; a command, by its op's verb, but an interactive one;
    and a chain, once for all its steps.
    """
    for contract in rules.visible_contracts():
        for verb in calls(contract):
            asked = verb in {'query', 'invoke'} and contract.request is not None and contract.variants
            for tag in rules.allowed_variants(contract) if asked else [None]:
                yield _Call(contract, verb, tag)
    chains: dict[tuple[str, type[Seared]], list[Command]] = {}
    for command in rules.visible_operations():
        if not isinstance(command, Command) or command.doc.interactive:
            continue
        if command.doc.chain is not None:
            chains.setdefault((command.under, command.doc.chain), []).append(command)
        else:
            yield _Call(command, op_of(command).value)
    for (_under, base), steps in chains.items():
        yield _Call(steps[0], 'see' if all(op_of(step) is Op.SEE for step in steps) else 'do', chain=base)


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


def _keeps(globs: Sequence[str], contract: Contract | Command) -> bool:
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


def interactive(rules: Rules) -> list[Command]:
    """The commands the policy shows that are never tools: interactive ones, for a person at a terminal."""
    return [o for o in rules.visible_operations() if isinstance(o, Command) and o.doc.interactive]


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


def _tool(call: _Call, rules: Rules) -> Tool:
    """The tool for ``call``: a verb on a contract (for a variant, when its request has a union), a command, a chain."""
    label = call.label(rules)
    name = _name(call.verb, label, call.tag)
    if not NAME.fullmatch(name):
        msg = f'{label}: its tool name, {name!r}, is longer than 64 characters'
        raise PolicyFileError(msg)
    if isinstance(call.operation, Command):
        return _command_tool(call, rules, name, label)
    contract, verb, tag = call.operation, call.verb, call.tag
    described = dict(variant_classes(contract))[tag] if tag is not None else None
    if described is None and verb == 'invoke':
        described = contract.request
    description = next(
        (line for cls in (described, contract.cls) if cls is not None and (line := summary_line(cls))), label
    )
    description += _DOES.get(verb, '')
    return Tool(name, description, _parameters(contract, rules, verb, tag, label), contract, verb, tag)


def _command_tool(call: _Call, rules: Rules, name: str, label: str) -> Tool:
    """A command's tool, or a chain's: parsed's schema of its arguments, in the dialect."""
    command = call.operation
    assert isinstance(command, Command)  # noqa: S101 -- _tool sends only commands here
    asks = command.doc.asks
    tags: list[str] = []
    if call.chain is None:
        described: type = command.cls
        schema = command_schema(command.cls)
    else:
        described = call.chain
        tree, shown = _chain_tree(rules, command, call.chain)
        steps = [step for step in introspect_chain(tree, call.chain).steps if step.command.schema.cls in shown]
        tags, asks = [step.tag for step in steps], any(step.command.asks for step in steps)
        schema = _shown_steps(chain_schema(tree, call.chain), tags)
    description = summary_line(described) or label
    if tags:
        description += f' Its `steps` go in order, each tagged by `step`: {", ".join(tags)}.'
    if asks:
        description += ' It may ask before it acts: `confirm` answers yes, where the policy allows.'
    parameters = _command_parameters(schema, _bound_wires(command, rules), label, asks=asks)
    return Tool(name, description, parameters, command, call.verb, None, call.chain)


def _chain_tree(rules: Rules, step: Command, base: type[Seared]) -> tuple[Tree, set[type]]:
    """A tree of ``base``'s steps in ``step``'s mount, every one of them, and the classes of those the policy shows."""
    steps = [c for c in rules.catalog.commands if c.doc.chain is base and c.under == step.under]
    return Tree([c.cls for c in steps]), {c.cls for c in steps if rules.visible(c)}


def _shown_steps(schema: dict[str, Any], tags: Sequence[str]) -> dict[str, Any]:
    """A chain's schema, its ``steps`` keeping only the branches whose tag is among ``tags``."""
    out = copy.deepcopy(schema)
    items = out.get('properties', {}).get('steps', {}).get('items', {})
    branches = items.get('anyOf')
    if isinstance(branches, list):
        items['anyOf'] = [b for b in branches if b.get('properties', {}).get('step', {}).get('const') in tags]
    return out


def _bound_wires(command: Command, rules: Rules) -> list[str]:
    """The wire names of the fields the policy's bound slots fill, on ``command``."""
    wires = {attr: wire for attr, wire, _field in command.cls.__seared_fields__}
    fields = {slot.name: slot.field for slot in command.doc.slots}
    return [wires[fields[slot]] for slot in rules.bound(command) if slot in fields and fields[slot] in wires]


def _command_parameters(schema: dict[str, Any], bound: Sequence[str], label: str, *, asks: bool) -> dict[str, Any]:
    """A command's (or a chain's) arguments as a tool's parameters, in the dialect.

    Its properties less the bound ones, each ``x-kind`` folded into a description, and ``confirm`` for one that asks.
    """
    folded = _folded(copy.deepcopy(schema))
    props = {name: prop for name, prop in folded.get('properties', {}).items() if name not in bound}
    required = [name for name in folded.get('required', []) if name not in bound]
    if asks:
        if 'confirm' in props:
            msg = f"{label}: 'confirm' is both a field and the parameter that answers its question; rename the field"
            raise PolicyFileError(msg)
        props['confirm'] = dict(CONFIRM)
    dialect = _Dialect(folded.get('$defs', {}), label)
    properties = {name: dialect.portable(prop, name) for name, prop in props.items()}
    parameters: dict[str, Any] = {'type': 'object', 'properties': _required_first(properties, required)}
    if required:
        parameters['required'] = [name for name in parameters['properties'] if name in required]
    parameters['additionalProperties'] = False
    return parameters


def _folded(node: Any) -> Any:  # a JSON Schema node
    """``node`` with each ``x-kind`` (and ``x-scope``) said in its description: ``the station's key (a station)``."""
    if isinstance(node, list):
        return [_folded(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _folded(value) for key, value in node.items()}
    kind = out.get('x-kind')
    if isinstance(kind, str):
        scope = out.get('x-scope')
        said = f'a {kind}' + (f', in the {scope} given' if isinstance(scope, str) else '')
        out['description'] = f'{out["description"]} ({said})' if out.get('description') else said
    return out


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
        unions = [key for key in _UNIONS if key in node]
        if unions and not (unions == ['anyOf'] and _tagged(node['anyOf'])):
            self._refuse(path, 'a union')
        out = {
            key: self._rewrite(key, value, node, path, seen)
            for key, value in node.items()
            if key not in _DROPPED and not key.startswith('x-')
        }
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
        if key == 'anyOf' and isinstance(value, list):
            return [self.portable(item, f'{path}|{at}', seen) for at, item in enumerate(value)]
        return value

    def _refuse(self, path: str, why: str) -> NoReturn:
        raise _Unsayable(self.where, path, why)


class _Unsayable(Exception):  # noqa: N818 -- not reported as it is: tools() gathers every one into one refusal
    """A field a tool's schema can't say: where it is, its path, why, and the optional root field it's in, if any."""

    def __init__(self, where: str, path: str, why: str) -> None:
        super().__init__(where, path, why)
        self.where, self.path, self.why = where, path, why
        self.field: str | None = None


def _tagged(branches: Any) -> bool:
    """Whether ``branches`` are each an object with properties, one of them a ``const``: a union a decoder holds."""
    return (
        isinstance(branches, list)
        and bool(branches)
        and all(
            isinstance(branch, dict)
            and branch.get('type') == 'object'
            and any(isinstance(prop, dict) and 'const' in prop for prop in branch.get('properties', {}).values())
            for branch in branches
        )
    )


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
    label = _Call(tool.operation, tool.verb, tool.variant, tool.chain).label(rules)
    return f'{tool.verb} {label}' + (f' variant {tool.variant!r}' if tool.variant is not None else '')
