"""Schemas: what a contract takes and gives, as JSON Schema, and the documents ``operations`` and ``describe`` print.

Every schema is seared's own (``seared.doc.json_schema`` and ``field_schema``), shaped by the policy:

- **A request's** is its load schema, closed (``unknown='raise'``), so valid input loads and an unknown key is
  refused. The fields the policy injects or hides are taken out, since a value given for one is refused, and so
  are the variants it denies: their branches, their tags, and the definitions only they used. It's the wire form,
  as the protocol defines it.
- **A reply's** is its dump schema: what ``dump`` writes, and so what a command prints.
- **A slot's** is its payload field's schema, when the slot is also a field; a string otherwise. A bound slot
  shows its value instead, since it can't be given.

**``describe`` is layered,** so even a large contract describes in a few kilobytes. By default the reply shows as a
field list (each top-level field's name, type, doc and constraints), and a request's root union folds to its tag
list, its branches left out. ``--reply`` prints the reply's whole schema, and ``--variant TAG`` one variant's; the
document's ``more`` names those commands.

**A path into a reply** (``--select``, ``wait --until``) is judged against the reply's dump schema by :func:`walk`,
before the call: the schema declares it, has no such field, or takes a shape the walker can't read.
"""

from __future__ import annotations

import copy
import dataclasses
from typing import TYPE_CHECKING, Any

from seared.doc import field_schema, introspect, is_seared_class, json_schema

from .catalog import Kind, wire_names
from .errors import RefusedError, UsageError
from .policy import Op, op_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .catalog import Contract
    from .policy import Rules

#: The verbs that act on each kind of contract, in this version.
VERBS: dict[tuple[Kind, Op], tuple[str, ...]] = {
    (Kind.RETAINED, Op.READ): ('fetch', 'listen', 'wait'),
    (Kind.STREAM, Op.READ): ('listen', 'wait'),
    (Kind.QUERYABLE, Op.QUERY): ('query',),
    (Kind.QUERYABLE, Op.INVOKE): ('invoke',),
}


def verbs(contract: Contract) -> tuple[str, ...]:
    """The verbs that act on ``contract`` in this version: none yet for a presence feed."""
    return VERBS.get((contract.kind, op_of(contract)), ())


def summary_line(cls: type) -> str:
    """The first line of ``cls``'s docstring, or nothing."""
    return (introspect(cls).summary or '') if is_seared_class(cls) else ''


# ---- the documents -----------------------------------------------------------------------------


def listing(rules: Rules) -> list[dict[str, Any]]:
    """What ``operations`` prints: every operation the policy shows, in order, one line of each."""
    return [
        {
            'name': rules.catalog.label(contract),
            'kind': contract.kind.value,
            'verbs': list(verbs(contract)),
            'topic': topic(contract, rules),
            'slots': [slot for slot in contract.slot_names if slot not in rules.bound(contract)],
            'summary': summary_line(contract.cls),
        }
        for contract in rules.visible_contracts()
    ]


def describe(contract: Contract, rules: Rules, *, reply: bool = False) -> dict[str, Any]:
    """What ``describe`` prints for one contract: what it is, its slots, and what it takes and gives.

    Args:
        contract: The contract.
        rules: The policy's rules, which shape the request and bind slots.
        reply: Print the reply's whole dump schema, rather than its field list.
    """
    label = rules.catalog.label(contract)
    more: dict[str, str] = {}
    doc: dict[str, Any] = {
        'name': rules.catalog.label(contract),
        'qualname': contract.qualname,
        'summary': summary_line(contract.cls),
        'kind': contract.kind.value,
        'op': op_of(contract).value,
        'verbs': list(verbs(contract)),
        'effect': contract.effect.value if contract.effect is not None else None,
        'topics': list(contract.templates),
        'slots': slot_schemas(contract, rules),
    }
    if contract.request is not None:
        request = request_schema(contract, rules)
        doc['injected'] = injected(contract, rules)
        if contract.variants:
            allowed = rules.allowed_variants(contract)
            doc['variants'] = [
                {'tag': tag, 'summary': summary_line(cls)} for tag, cls in variant_classes(contract) if tag in allowed
            ]
            request = fold_unions(request) if request is not None else None
            more['variant'] = f"tooled describe {label} --variant TAG: one variant's fields"
        doc['request'] = request
    if reply:
        doc['reply'] = json_schema(contract.cls, mode='dump')
    else:
        doc['reply'] = field_list(contract.cls)
        more['reply'] = f"tooled describe {label} --reply: the reply's whole schema"
    if more:
        doc['more'] = more
    return doc


def describe_variant(contract: Contract, tag: str, rules: Rules) -> dict[str, Any]:
    """What ``describe X --variant TAG`` prints: one variant of the request's union, and its fields' schema.

    Raises:
        UsageError: ``contract`` has no such tag.
        RefusedError: The policy denies it everywhere.
    """
    classes = dict(variant_classes(contract))
    if tag not in classes:
        shown = ', '.join(rules.allowed_variants(contract)) or 'none'
        msg = f'{rules.catalog.label(contract)} has no variant {tag!r}; the variants the policy shows: {shown}'
        raise UsageError(msg)
    if tag not in rules.allowed_variants(contract):
        msg = f'the policy denies variant {tag!r} of {rules.catalog.label(contract)}'
        raise RefusedError(msg)
    cls = classes[tag]
    schema = json_schema(cls, mode='load', unknown='raise')
    drop_fields(schema, list(rules.hidden(contract, tag)))
    return {
        'name': rules.catalog.label(contract),
        'variant': tag,
        'summary': summary_line(cls),
        'schema': prune_defs(schema),
    }


# ---- the pieces --------------------------------------------------------------------------------


def topic(contract: Contract, rules: Rules) -> str:
    """``contract``'s topic, its bound slots filled and the rest left as ``{slot}``."""
    out = contract.templates[0]
    for slot, value in rules.bound(contract).items():
        out = out.replace(f'{{{slot}}}', value).replace(f'{{{slot}**}}', value)
    return out


def slot_schemas(contract: Contract, rules: Rules) -> dict[str, Any]:
    """Each slot's schema, or its bound value."""
    bound = rules.bound(contract)
    fields = {attr: field for attr, _wire, field in contract.cls.__seared_fields__}
    out: dict[str, Any] = {}
    for slot in contract.slots:
        if slot.name in bound:
            out[slot.name] = {'bound': bound[slot.name]}
            continue
        if slot.multi:
            schema: dict[str, Any] = {'type': 'string', 'description': 'one chunk or more: `a/b`'}
        elif slot.is_field and slot.name in fields:
            defs: dict[str, Any] = {}
            schema = field_schema(fields[slot.name], mode='load', defs=defs)
            if defs:
                schema['$defs'] = defs
        else:
            schema = {'type': 'string'}
        out[slot.name] = schema
    return out


def injected(contract: Contract, rules: Rules) -> list[str]:
    """The fields of ``contract``'s request the policy injects."""
    if contract.request is None:
        return []
    names = wire_names(contract.request)
    return [name for name in names if name in rules.policy.inject]


def request_schema(contract: Contract, rules: Rules, tag: str | None = None) -> dict[str, Any] | None:
    """``contract``'s request load schema, closed, without the injected or hidden fields or the denied variants.

    The hidden fields are those of its own the policy hides from every call, or from a call carrying ``tag``.
    """
    if contract.request is None:
        return None
    schema = json_schema(contract.request, mode='load', unknown='raise')
    drop_fields(schema, [*injected(contract, rules), *rules.hidden(contract, tag)])
    denied = set(contract.variants) - set(rules.allowed_variants(contract))
    if denied:
        schema = drop_variants(schema, denied)
    return prune_defs(schema)


def variant_classes(contract: Contract) -> list[tuple[str, type]]:
    """Each tag of ``contract``'s request unions, with its class, in order."""
    if contract.request is None or not is_seared_class(contract.request):
        return []
    out: dict[str, type] = {}
    for field in introspect(contract.request).fields:
        for variant in field.variants or ():
            out.setdefault(variant.tag, variant.cls)
    return list(out.items())


def field_list(cls: type) -> list[dict[str, Any]]:
    """Each top-level field of ``cls``: its wire name, its type, and its doc and its constraints, when it has them.

    The constraints are seared's, by the names a declaration gives them (``ge``, ``max_length``, ``pattern``…), in
    seared's order; on a list or a map, they hold for each value.
    """
    if not is_seared_class(cls):
        return []
    out = []
    for field in introspect(cls).fields:
        entry: dict[str, Any] = {'name': field.wire_key or field.attr, 'type': field.type_str}
        if field.doc:
            entry['doc'] = field.doc
        if field.constraints:
            entry['constraints'] = dict(field.constraints)
        out.append(entry)
    return out


def tag_keys(cls: type) -> list[str]:
    """The wire keys that carry the tags of ``cls``'s root unions, read from its load schema.

    A union always puts its tag at its parent's root, nested envelope or flat, so the key is found in the raw
    request before it loads.
    """
    keys = []
    for envelope in json_schema(cls, mode='load').get('allOf', ()):
        branches = envelope.get('allOf')
        for branch in branches if isinstance(branches, list) else ():
            key, _tag = branch_tag(branch)
            if key is not None:
                keys.append(key)
                break
    return keys


def fold_unions(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy of a request schema with each root union folded to its tags.

    The branches, and the definitions only they used, are left out: it documents the request, and no longer
    validates a variant's fields.
    """
    out = copy.deepcopy(schema)
    for envelope in out.get('allOf', ()):
        if isinstance(envelope.get('allOf'), list):
            del envelope['allOf']
            note = "a variant's fields: describe --variant TAG"
            envelope['description'] = f'{envelope["description"]}; {note}' if envelope.get('description') else note
    return prune_defs(out)


def drop_fields(schema: dict[str, Any], names: list[str]) -> None:
    """Take ``names`` out of an object schema's ``properties`` and ``required``, in place."""
    properties = schema.get('properties', {})
    for name in names:
        properties.pop(name, None)
    if 'required' in schema:
        schema['required'] = [name for name in schema['required'] if name not in names]
        if not schema['required']:
            del schema['required']


def drop_variants(schema: dict[str, Any], denied: set[str]) -> dict[str, Any]:
    """A copy of ``schema`` without the union variants ``denied`` names.

    seared writes each union at a request's root as one entry of its ``allOf``: the tag's ``enum``, then one
    ``if``/``then`` branch per tag. A denied tag leaves the ``enum``, its branch goes, and so do the definitions
    nothing refers to any more.
    """
    out = copy.deepcopy(schema)
    for envelope in out.get('allOf', ()):
        branches = envelope.get('allOf')
        if not isinstance(branches, list):
            continue
        kept, keys = [], set()
        for branch in branches:
            tag_key, tag = branch_tag(branch)
            if tag_key is not None:
                keys.add(tag_key)
            if tag not in denied:
                kept.append(branch)
        envelope['allOf'] = kept
        for key in keys:
            prop = envelope.get('properties', {}).get(key, {})
            if 'enum' in prop:
                prop['enum'] = [tag for tag in prop['enum'] if tag not in denied]
    return prune_defs(out)


def branch_tag(branch: Any) -> tuple[str | None, str | None]:  # a JSON Schema node
    """The tag key and tag an ``if``/``then`` branch selects on: ``if: {properties: {key: {const: tag}}}``."""
    condition = branch.get('if', {}) if isinstance(branch, dict) else {}
    for key, rule in condition.get('properties', {}).items():
        if isinstance(rule, dict) and 'const' in rule:
            return key, rule['const']
    return None, None


def prune_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` with only the ``$defs`` it reaches, transitively."""
    defs = schema.get('$defs')
    if not defs:
        return schema
    keep: set[str] = set()
    todo = _refs({k: v for k, v in schema.items() if k != '$defs'})
    while todo:
        name = todo.pop()
        if name in keep or name not in defs:
            continue
        keep.add(name)
        todo |= _refs(defs[name])
    schema['$defs'] = {name: defs[name] for name in defs if name in keep}
    if not schema['$defs']:
        del schema['$defs']
    return schema


def _refs(node: Any) -> set[str]:  # a JSON Schema node
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get('$ref')
        if isinstance(ref, str) and ref.startswith('#/$defs/'):
            found.add(ref.removeprefix('#/$defs/'))
        for value in node.values():
            found |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs(value)
    return found


# ---- paths into a reply ------------------------------------------------------------------------------

#: A step into a list's items, in a path :func:`walk` takes: ``--select``'s ``[]``.
ITEMS = '[]'

#: A scalar's JSON Schema types: a path that goes on past one names nothing.
_SCALARS = frozenset({'string', 'number', 'integer', 'boolean', 'null'})


@dataclasses.dataclass(frozen=True)
class Walk:
    """What :func:`walk` made of a path.

    Args:
        declared: Whether the schema has the path; ``None`` when it takes a shape the walker can't read.
        at: The index of the first part the schema doesn't have (or can't tell), else the path's length.
        names: The names the schema offers where the path left it, for a hint.
        listed: Whether the path left it at a list, which only a step into its items enters.
    """

    declared: bool | None
    at: int
    names: tuple[str, ...] = ()
    listed: bool = False  # the path left the schema at a list, which a step into its items would have entered


@dataclasses.dataclass
class _Offer:
    """What a schema node lets a path name next."""

    names: dict[str, Any] = dataclasses.field(default_factory=dict)  # a field's name, and its schema
    items: Any = None  # a list's item schema
    keys: Any = None  # a map's value schema: any key leads to it
    known: bool = True  # False: a shape the walker doesn't read

    def add(self, names: dict[str, Any]) -> None:
        """Offer ``names``; a name offered already (a nested envelope's payload key, in each variant) is either."""
        for name, node in names.items():
            self.names[name] = node if name not in self.names else {'anyOf': [self.names[name], node]}

    def merge(self, other: _Offer) -> None:
        """Offer what ``other`` offers too: a union's variants, or the branches of an ``anyOf``."""
        self.add(other.names)
        self.keys = self.keys if other.keys is None else other.keys
        self.items = self.items if other.items is None else other.items


def walk(schema: dict[str, Any], parts: Sequence[str], *, indexes: bool = False) -> Walk:
    """Judge a path against a dump schema (seared's, from ``json_schema(cls, mode='dump')``).

    Each part is a field's name, or :data:`ITEMS` to step into a list. A map takes any key. A ``Bytes`` field takes
    ``bytes`` and ``sha256``, the summary tooled prints in its place. A union offers the fields of every variant.

    Args:
        schema: The whole schema, its ``$defs`` included.
        parts: The path.
        indexes: Whether a whole number steps into a list, as in ``wait --until`` (``ships.0.name``).
    """
    node: Any = schema
    for at, part in enumerate(parts):
        offer = _offer(schema, node)
        if not offer.known:
            return Walk(declared=None, at=at)
        if part == ITEMS or (indexes and part.isdigit() and offer.items is not None):
            if offer.items is None:
                return Walk(declared=False, at=at, names=tuple(offer.names))
            node = offer.items
        elif part in offer.names:
            node = offer.names[part]
        elif offer.keys is not None:
            node = offer.keys
        else:
            return Walk(declared=False, at=at, names=tuple(offer.names), listed=offer.items is not None)
    return Walk(declared=True, at=len(parts))


def _offer(schema: dict[str, Any], node: Any) -> _Offer:  # a JSON Schema node
    """What ``node`` lets a path name next: its properties, and its unions' variants' and branches'."""
    node = _deref(schema, node)
    if not isinstance(node, dict):
        return _Offer(known=False)
    if node.get('contentEncoding') == 'base64':
        return _Offer(names={'bytes': {'type': 'integer'}, 'sha256': {'type': 'string'}})
    offer = _Offer()
    found = False
    if isinstance(node.get('properties'), dict):
        offer.add(node['properties'])
        found = True
    if isinstance(node.get('additionalProperties'), dict):
        offer.keys, found = node['additionalProperties'], True
    if node.get('type') == 'array' and isinstance(node.get('items'), dict):
        offer.items, found = node['items'], True
    for part in [*_union_parts(node), *_alternatives(node)]:
        inner = _offer(schema, part)
        if not inner.known:
            return inner
        offer.merge(inner)
        found = True
    if found or _is_scalar(node):
        return offer  # a scalar offers nothing further to name
    return _Offer(known=False)


def _alternatives(node: dict[str, Any]) -> list[Any]:  # JSON Schema nodes
    """A node's ``anyOf`` or ``oneOf`` branches, but ``null``'s: any of them may hold the path."""
    branches = [*node.get('anyOf', ()), *node.get('oneOf', ())]
    return [branch for branch in branches if isinstance(branch, dict) and branch.get('type') != 'null']


def _union_parts(node: dict[str, Any]) -> list[Any]:  # JSON Schema nodes
    """A node's ``allOf`` entries, and each ``if``/``then`` branch's ``then``: a union's envelope and variants."""
    entries = [entry for entry in node.get('allOf', ()) if isinstance(entry, dict)]
    return [entry for entry in entries if 'if' not in entry] + [entry['then'] for entry in entries if 'then' in entry]


def _is_scalar(node: dict[str, Any]) -> bool:  # a JSON Schema node
    kind = node.get('type')
    if isinstance(kind, list):
        return bool(kind) and set(kind) <= _SCALARS
    return kind in _SCALARS or 'enum' in node or 'const' in node


def _deref(schema: dict[str, Any], node: Any) -> Any:  # a JSON Schema node
    """``node``, its ``$ref`` (to the schema's own ``$defs``) followed."""
    seen = set()
    while isinstance(node, dict) and isinstance(node.get('$ref'), str) and node['$ref'] not in seen:
        seen.add(node['$ref'])
        name = node['$ref'].removeprefix('#/$defs/')
        node = schema.get('$defs', {}).get(name, {})
    return node
