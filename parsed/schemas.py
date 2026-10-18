"""Schemas: what a contract takes and gives, as JSON Schema, and the documents ``contracts`` and ``describe`` print.

Every schema is seared's own (``seared.doc.json_schema`` and ``field_schema``), shaped by the policy:

- **A request's** is its load schema, closed (``unknown='raise'``), so valid input loads and an unknown key is
  refused. The fields the policy injects are taken out, since a value given for one is refused, and so are the
  variants it denies: their branches, their tags, and the definitions only they used. It's the wire form, as the
  protocol defines it.
- **A reply's** is its dump schema: what ``dump`` writes, and so what a command prints.
- **A slot's** is its payload field's schema, when the slot is also a field; a string otherwise. A bound slot
  shows its value instead, since it can't be given.

**``describe`` is layered,** so even a large contract describes in a few kilobytes. By default the reply shows as a
field list (each top-level field's name, type and doc), and a request's root union folds to its tag list, its
branches left out. ``--reply`` prints the reply's whole schema, and ``--variant TAG`` one variant's; the document's
``more`` names those commands.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from seared.doc import field_schema, introspect, is_seared_class, json_schema

from .catalog import Kind, wire_names
from .errors import RefusedError, UsageError
from .policy import Op, op_of

if TYPE_CHECKING:
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
    """What ``contracts`` prints: every contract the policy shows, in order, one line of each."""
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
            more['variant'] = f"parsed describe {label} --variant TAG: one variant's fields"
        doc['request'] = request
    if reply:
        doc['reply'] = json_schema(contract.cls, mode='dump')
    else:
        doc['reply'] = field_list(contract.cls)
        more['reply'] = f"parsed describe {label} --reply: the reply's whole schema"
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
    return {
        'name': rules.catalog.label(contract),
        'variant': tag,
        'summary': summary_line(cls),
        'schema': json_schema(cls, mode='load', unknown='raise'),
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


def request_schema(contract: Contract, rules: Rules) -> dict[str, Any] | None:
    """``contract``'s request load schema, closed, without the injected fields or the denied variants."""
    if contract.request is None:
        return None
    schema = json_schema(contract.request, mode='load', unknown='raise')
    drop_fields(schema, injected(contract, rules))
    denied = set(contract.variants) - set(rules.allowed_variants(contract))
    if denied:
        schema = drop_variants(schema, denied)
    return schema


def variant_classes(contract: Contract) -> list[tuple[str, type]]:
    """Each tag of ``contract``'s request unions, with its class, in order."""
    if contract.request is None or not is_seared_class(contract.request):
        return []
    out: dict[str, type] = {}
    for field in introspect(contract.request).fields:
        for variant in field.variants or ():
            out.setdefault(variant.tag, variant.cls)
    return list(out.items())


def field_list(cls: type) -> list[dict[str, str]]:
    """Each top-level field of ``cls``: its wire name, its type, and its doc, when it has one."""
    if not is_seared_class(cls):
        return []
    out = []
    for field in introspect(cls).fields:
        entry = {'name': field.wire_key or field.attr, 'type': field.type_str}
        if field.doc:
            entry['doc'] = field.doc
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
            key, _tag = _branch_tag(branch)
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
            tag_key, tag = _branch_tag(branch)
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


def _branch_tag(branch: Any) -> tuple[str | None, str | None]:  # a JSON Schema node
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
