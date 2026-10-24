"""The policy's rules, checked against a catalog: which operations it shows, and whether it allows a call.

Each operation takes one op (:func:`op_of`): a contract a wire op, a command a tree op. A rule matches an operation
when its ``ops`` name that op and one of its ``operations`` globs matches the operation's name. Then its keys decide,
with Zenoh's own matching (``zeared.keyexpr``), against a contract's topics or a command's keys (its phrases', led by
its mount's prefix: ``relay/run``, ``fleet/{fleet}/mine``):

- **What's shown.** An operation is shown when an allow rule's keys intersect one of its topics or keys (as the
  policy's ``[bind]`` renders it) and no deny rule covers all of them.
- **What's allowed.** A call renders one key: a contract's selector, the topic with the given and bound slots filled
  and the rest widened; a command's instance key, from parsed (:meth:`Rules.key`). It's allowed when an allow rule's
  keys include the whole key and no deny rule's keys intersect it. Replies carry no keys to filter by afterwards, so
  a selector that could reach a denied key is refused outright: narrowing the slots is the way through.
- **Asking first.** A command that asks before it acts is answered yes only where ``confirm`` is allowed too, at
  the same key (``check(…, confirm=True)``).

**A command's key is safe or refused** (:meth:`Rules.key`): a slot's value holding ``/``, ``*``, ``$``, ``?`` or ``#``
would make it a pattern, or more chunks than its phrase has, so it's refused, naming the kind whose resolver should
map the value to a key. A slot ``[bind]`` fills must hold the bound value.

**Deny wins.** **Variants:** a rule with ``variants`` covers only the request-union tags its globs match. A deny
rule with variants refuses those tags, not the contract; an allow rule with variants allows only those tags.

**Fields:** a deny rule with ``fields`` refuses no call, only the request fields its globs match: the optional
fields at the request's root, its own and (for a call that carries one) a flat union variant's. Such a field is
hidden: a call that gives it is refused, before the request loads, and the tools and ``describe`` leave it out. A
required field can't be left out, so a glob passes over it, and a name without a glob that names one is refused.
With ``variants`` too, only those variants' calls are covered.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

from parsed.doc import command_key
from parsed.kinds import Named
from zeared import Effect, keyexpr

from ..catalog import SLOT, Command, Contract, Kind, render, top_fields
from ..errors import PolicyFileError, RefusedError, UsageError
from .config import TREE_OPS, Op

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from parsed import Command as CommandClass
    from parsed.names import Resolver

    from ..catalog import Catalog
    from .config import Policy, Rule

#: What a rule guards: a contract on the wire, or a command on the tree.
type Guarded = Contract | Command

_GLOB = frozenset('*?[')

#: What a command's key can't hold: a key's separator, and what a key expression reads as a wildcard or more.
_UNSAFE = frozenset('/*$?#')


def op_of(operation: Guarded) -> Op:
    """The op ``operation`` takes: what a rule's ``ops`` name to cover it.

    On the wire: ``read`` for a retained class, a stream or a presence feed; ``query`` for a queryable that declares
    ``EFFECT = READ``; ``invoke`` for any other queryable. On the tree: ``see`` for a command that declares
    ``EFFECT = READ``; ``do`` for any other. An undeclared effect is never a read, so the default fails safe.
    """
    if isinstance(operation, Command):
        return Op.SEE if operation.effect is Effect.READ else Op.DO
    if operation.kind is not Kind.QUERYABLE:
        return Op.READ
    return Op.QUERY if operation.effect is Effect.READ else Op.INVOKE


def templates(operation: Guarded) -> tuple[str, ...]:
    """What a rule's keys are matched against: a contract's topic templates, or a command's key templates."""
    return operation.keys if isinstance(operation, Command) else operation.templates


class Rules:
    """A policy's rules over a catalog.

    Args:
        policy: The loaded policy.
        catalog: The contracts of its protocol.

    Raises:
        PolicyFileError: A rule names an operation, a variant or a field the protocol doesn't have, or hides a
            field a request requires; a bound slot is no contract's, or an injected field is no request's.
    """

    def __init__(self, policy: Policy, catalog: Catalog) -> None:
        self.policy = policy
        self.catalog = catalog
        self._check_references()

    # ---- what's shown --------------------------------------------------------------------------

    def bound(self, operation: Guarded) -> dict[str, str]:
        """The bound slots ``operation``'s topics or keys have."""
        return {slot: value for slot, value in self.policy.bind.items() if slot in operation.slot_names}

    def shapes(self, operation: Guarded) -> tuple[str, ...]:
        """Every topic or key of ``operation`` as a key expression, its bound slots filled and the rest widened."""
        bound = self.bound(operation)
        return tuple(render(template, bound) for template in templates(operation))

    def visible(self, contract: Guarded) -> bool:
        """Whether the policy shows ``contract`` (or a command) at all."""
        op, shapes = op_of(contract), self.shapes(contract)
        allowed = any(
            _covers(rule, op, contract)
            and _any_tag(rule, contract)
            and any(keyexpr.intersects(key, shape) for key in rule.keys for shape in shapes)
            for rule in self.policy.allow
        )
        hidden = any(
            _covers(rule, op, contract)
            and rule.variants is None
            and rule.fields is None
            and all(any(keyexpr.includes(key, shape) for key in rule.keys) for shape in shapes)
            for rule in self.policy.deny
        )
        return allowed and not hidden

    def visible_contracts(self) -> list[Contract]:
        """Every contract the policy shows, in the catalog's order."""
        return [contract for contract in self.catalog.contracts if self.visible(contract)]

    def visible_operations(self) -> list[Guarded]:
        """Every operation the policy shows, contracts and commands, in the catalog's order."""
        return [o for o in self.catalog.operations if isinstance(o, Contract | Command) and self.visible(o)]

    def allowed_variants(self, contract: Contract) -> tuple[str, ...]:
        """The tags of ``contract``'s request unions the policy shows: allowed somewhere, denied not everywhere."""
        op, shapes = op_of(contract), self.shapes(contract)
        out = []
        for tag in contract.variants:
            allowed = any(
                _covers(rule, op, contract)
                and (rule.variants is None or _tag(rule, tag))
                and any(keyexpr.intersects(key, shape) for key in rule.keys for shape in shapes)
                for rule in self.policy.allow
            )
            denied = any(
                _covers(rule, op, contract)
                and rule.variants is not None
                and rule.fields is None
                and _tag(rule, tag)
                and all(any(keyexpr.includes(key, shape) for key in rule.keys) for shape in shapes)
                for rule in self.policy.deny
            )
            if allowed and not denied:
                out.append(tag)
        return tuple(out)

    def hidden(self, contract: Contract, tag: str | None = None) -> tuple[str, ...]:
        """The request fields the policy hides on ``contract``, in a call carrying the variant ``tag``.

        They're the optional fields at the request's root, its own and ``tag``'s, that a deny rule's ``fields``
        match on every topic: a rule with ``variants`` hides them only when it names ``tag``.
        """
        op, shapes = op_of(contract), self.shapes(contract)
        hiding = [
            rule
            for rule in self.policy.deny
            if rule.fields is not None
            and _covers(rule, op, contract)
            and (rule.variants is None or (tag is not None and _tag(rule, tag)))
            and all(any(keyexpr.includes(key, shape) for key in rule.keys) for shape in shapes)
        ]
        return tuple(name for name in _optional(contract, tag) if any(_field(rule, name) for rule in hiding))

    # ---- what's allowed ------------------------------------------------------------------------

    def check(
        self,
        contract: Guarded,
        selector: str,
        *,
        variant: str | None = None,
        fields: Collection[str] = (),
        confirm: bool = False,
    ) -> None:
        """Whether the policy allows a call on ``contract`` (or a command) that renders ``selector``.

        Args:
            contract: The contract, or the command, called.
            selector: The key expression the call reaches: a contract's selector, or a command's key.
            variant: The tag of the request's union the call carries, if it carries one.
            fields: The fields at the root of the request the call gives, as given.
            confirm: Whether the call answers yes to a command that asks first, which ``confirm`` must allow too.

        Raises:
            RefusedError: A deny rule's keys intersect the selector (for its variant, if it names variants), or
                hide a field the call gives; or no allow rule's keys include it. Then, when a slot left open is one
                an allow rule's keys fix, the message names it, since filling it is what the call needs, and the
                value too, when the allow rules that reach the selector all fix it to one. With ``confirm``, the
                same for ``confirm``.
        """
        self._check_op(op_of(contract), contract, selector, variant=variant, fields=fields)
        if confirm:
            self._check_op(Op.CONFIRM, contract, selector)

    def _check_op(
        self, op: Op, contract: Guarded, selector: str, *, variant: str | None = None, fields: Collection[str] = ()
    ) -> None:
        """:meth:`check`, for one op."""
        label = self.catalog.label(contract)
        for index, rule in enumerate(self.policy.deny):
            if not _covers(rule, op, contract) or not any(keyexpr.intersects(key, selector) for key in rule.keys):
                continue
            if rule.fields is not None and isinstance(contract, Contract):
                _refuse_hidden(rule, index, contract, variant, fields)
            elif rule.variants is None:
                msg = f'{op} on {label} at {selector} is denied by deny[{index}]; narrow the slots if it reaches more'
                raise RefusedError(msg)
            elif variant is not None and _tag(rule, variant):
                msg = f'{op} on {label} with variant {variant!r} is denied by deny[{index}]'
                raise RefusedError(msg)
        allows = [
            rule
            for rule in self.policy.allow
            if _covers(rule, op, contract) and (rule.variants is None or (variant is not None and _tag(rule, variant)))
        ]
        if not any(keyexpr.includes(key, selector) for rule in allows for key in rule.keys):
            msg = f'no allow rule covers {op} on {label} at {selector}{_fill_hint(contract, selector, allows)}'
            raise RefusedError(msg)

    def key(self, command: Command, instance: CommandClass, names: Mapping[str, Resolver] | None = None) -> str:
        """The key a call of ``command`` reaches: parsed's for the instance, led by its mount's prefix, slots judged.

        Args:
            command: The command called.
            instance: Its instance, as its line read.
            names: The resolvers the line was read with, so the key holds the values' keys.

        Raises:
            RefusedError: A slot's value holds ``/``, ``*``, ``$``, ``?`` or ``#``, which a key can't: the message
                names the kind whose resolver should map it to a key. Or a slot ``[bind]`` fills holds another
                value. ``path`` names the slot.
        """
        label, bound = self.catalog.label(command), self.bound(command)
        for slot in command.doc.slots:
            if slot.many or slot.rest:
                continue  # a slot of several words is no part of a key
            value: Any = getattr(instance, slot.field, None)
            text = '' if value is None else str(value)
            unsafe = sorted(set(text) & _UNSAFE)
            if unsafe:
                kind = slot.kind
                fix = (
                    f'map it to a key in the resolver of {kind.category!r}'
                    if isinstance(kind, Named)
                    else 'read the slot as a name, whose resolver maps it to a key'
                )
                msg = f"{label}: slot {slot.name!r} holds {text!r}, and a key can't hold {unsafe[0]!r}: {fix}"
                raise RefusedError(msg, path=(slot.name,))
            if slot.name in bound and text != bound[slot.name]:
                msg = f'the policy binds {slot.name!r} to {bound[slot.name]!r}'
                raise RefusedError(msg, path=(slot.name,))
        return '/'.join(part for part in (*command.under.split(), command_key(instance, names)) if part)

    def fixed(self, contract: Guarded) -> dict[str, str]:
        """The slots ``contract``'s topics leave open that the allow rules let only one value fill, with that value.

        Read on each topic as ``[bind]`` renders it, against the keys of the allow rules that cover the contract
        and reach that topic. Every call must give such a slot that one value, so a policy would rather bind it. A
        slot two topics fix to different values is left out.
        """
        op = op_of(contract)
        allows = [rule for rule in self.policy.allow if _covers(rule, op, contract) and _any_tag(rule, contract)]
        found: dict[str, str | None] = {}
        for shape in self.shapes(contract):
            keys = _reaching(allows, shape)
            for name, at, multi in _open_slots(contract, shape):
                value = _only_value(keys, at, multi=multi)
                found[name] = value if found.get(name, value) == value else None
        return {name: value for name, value in found.items() if value is not None}

    # ---- references --------------------------------------------------------------------------

    def _check_references(self) -> None:
        """A name without a glob must name something: an operation, a variant, a slot, a request field."""
        for side in ('allow', 'deny'):
            for index, rule in enumerate(getattr(self.policy, side)):
                self._check_rule(rule, (side, index))
        slots = self.catalog.slot_names
        for slot in self.policy.bind:
            if slot not in slots:
                msg = f'binds {slot!r}, which no contract or command has as a slot'
                raise PolicyFileError(msg, path=('bind', slot))
        fields = self.catalog.request_fields
        for name in self.policy.inject:
            if name not in fields:
                msg = f'injects {name!r}, which no request declares'
                raise PolicyFileError(msg, path=('inject', name))

    def _check_rule(self, rule: Rule, where: tuple[str | int, ...]) -> None:
        matched: dict[str, Contract] = {}
        tree = bool(set(rule.ops) & TREE_OPS)
        for at, pattern in enumerate(rule.operations):
            found = self.catalog.matching(pattern)
            if not (_GLOB & set(pattern)):
                try:
                    found = [self.catalog.get(pattern)]
                except UsageError as exc:
                    raise PolicyFileError(exc.message, path=(*where, 'operations', at)) from exc
                if isinstance(found[0], Command) is not tree:
                    named, route = self.catalog.label(found[0]), 'tree' if tree else 'wire'
                    what = 'a command' if isinstance(found[0], Command) else 'a contract'
                    msg = f"{named} is {what}, which the {route}'s ops never reach"
                    raise PolicyFileError(msg, path=(*where, 'operations', at))
            matched |= {o.qualname: o for o in found if isinstance(o, Contract)}
        if rule.variants is not None:
            tags = {tag for contract in matched.values() for tag in contract.variants}
            for at, pattern in enumerate(rule.variants):
                if not (_GLOB & set(pattern)) and pattern not in tags:
                    msg = f'variant {pattern!r} is no tag of the contracts this rule names'
                    raise PolicyFileError(msg, path=(*where, 'variants', at))
        if rule.fields is not None:
            self._check_fields(rule, [c for c in matched.values() if op_of(c) in rule.ops], where)

    def _check_fields(self, rule: Rule, covered: Sequence[Contract], where: tuple[str | int, ...]) -> None:
        """A deny rule's ``fields``: a name without a glob is a field of a request it covers, and an optional one.

        It's read where the rule reaches: the request's own fields, and those of each variant it covers.
        """
        found = [
            (contract, tag, fields)
            for contract in covered
            if contract.request is not None
            for tag, fields in top_fields(contract.request).items()
            if tag is None or rule.variants is None or _tag(rule, tag)
        ]
        for at, name in enumerate(rule.fields or ()):
            if _GLOB & set(name):
                continue
            if not any(name in fields for *_, fields in found):
                msg = f'field {name!r} is no field at the root of a request this rule covers'
                raise PolicyFileError(msg, path=(*where, 'fields', at))
            for contract, tag, fields in found:
                if fields.get(name):
                    whose = self.catalog.label(contract) + (f' variant {tag!r}' if tag is not None else '')
                    msg = f"hides {name!r}, which {whose} requires, so a call can't leave it out; deny {whose} instead"
                    raise PolicyFileError(msg, path=(*where, 'fields', at))


def _fill_hint(contract: Guarded, selector: str, allows: Sequence[Rule]) -> str:
    """What to say about the slots ``selector`` leaves open that an allow rule's keys fix, if any.

    A slot the call left open renders as ``*`` (``*/**`` for a trailing ``{name**}``). One is named when an allow
    rule's key intersects the selector and holds a literal chunk where the slot is, with no ``**`` before it: a
    value there is what the rule wants. A slot every rule leaves wild already is no help, so it isn't named. The
    value is given too when every key that reaches the selector fixes the slot to the same one; else it's ``…``.
    """
    keys = _reaching(allows, selector)
    named = [
        (name, _only_value(keys, at, multi=multi) or '…')
        for name, at, multi in _open_slots(contract, selector)
        if any(_fixes(key, at) for key in keys)
    ]
    if not named:
        return ''
    give = ' '.join(f'{name}={value}' for name, value in named)
    if len(named) == 1:
        return f': slot {named[0][0]!r} is open; give {give}, or bind it in [bind]'
    listed = ', '.join(repr(name) for name, _ in named[:-1]) + f' and {named[-1][0]!r}'
    return f': slots {listed} are open; give {give}, or bind them in [bind]'


def _reaching(allows: Sequence[Rule], selector: str) -> list[list[str]]:
    """The keys of ``allows`` that intersect ``selector``, each split into its chunks."""
    return [key.split('/') for rule in allows for key in rule.keys if keyexpr.intersects(key, selector)]


def _fixes(key: Sequence[str], at: int) -> bool:
    """Whether ``key`` holds a literal chunk at ``at``, with no ``**`` before it: a slot there wants that value."""
    return at < len(key) and '**' not in key[: at + 1] and not _wild(key[at])


def _only_value(keys: Sequence[Sequence[str]], at: int, *, multi: bool) -> str | None:
    """The one value every key in ``keys`` fixes for a slot at chunk ``at``, or ``None`` when they don't agree.

    A key fixes a slot's value when it holds literal chunks there: one chunk, or for a trailing multi-chunk slot
    every chunk to the key's end. A key that leaves any of it wild lets other values in, so then there's none.
    """
    values = set()
    for key in keys:
        chunks = key[at:] if multi else key[at : at + 1]
        values.add('/'.join(chunks) if _fixes(key, at) and not any(_wild(c) for c in chunks) else None)
    return values.pop() if len(values) == 1 and None not in values else None


def _open_slots(contract: Guarded, selector: str) -> list[tuple[str, int, bool]]:
    """Each slot ``selector`` leaves open: its name, its chunk's index, and whether it takes the rest of the key.

    It's read against the template ``selector`` was rendered from. A template fits when its literal chunks are the
    selector's own; a slot sharing a chunk with other text can't be told apart, so a template with one is passed
    over, and nothing is named for it.
    """
    chunks = selector.split('/')
    for template in templates(contract):
        parts = template.split('/')
        slots = [SLOT.fullmatch(part) for part in parts]
        multi = slots[-1] is not None and slots[-1].group(2) is not None
        if len(chunks) != len(parts) and not (multi and len(chunks) > len(parts)):
            continue
        if any(slot is None and part != chunk for slot, part, chunk in zip(slots, parts, chunks, strict=False)):
            continue
        tail = len(parts) - 1
        return [
            (slot.group(1), at, multi and at == tail)
            for at, slot in enumerate(slots)
            if slot is not None and (chunks[at:] == ['*', '**'] if multi and at == tail else chunks[at] == '*')
        ]
    return []


def _refuse_hidden(rule: Rule, index: int, contract: Contract, variant: str | None, given: Collection[str]) -> None:
    """Refuse a call whose request gives a field ``rule`` hides: an optional one at its root that ``rule`` names.

    Raises:
        RefusedError: It gives one, or more; each is named, and the first is its path.
    """
    if rule.variants is not None and (variant is None or not _tag(rule, variant)):
        return
    optional = _optional(contract, variant)
    named = [name for name in given if name in optional and _field(rule, name)]
    if not named:
        return
    if len(named) == 1:
        msg = f'{named[0]!r} is hidden by the policy (deny[{index}]); leave it out of the request'
    else:
        listed = ', '.join(repr(name) for name in named[:-1]) + f' and {named[-1]!r}'
        msg = f'{listed} are hidden by the policy (deny[{index}]); leave them out of the request'
    raise RefusedError(msg, path=(named[0],))


def _optional(contract: Contract, tag: str | None) -> list[str]:
    """The optional fields at the root of ``contract``'s request, its own and then ``tag``'s, in order."""
    if contract.request is None:
        return []
    found = top_fields(contract.request)
    own, variant = found.get(None, {}), found.get(tag, {}) if tag is not None else {}
    return [name for name, required in (*own.items(), *variant.items()) if not required]


def _field(rule: Rule, name: str) -> bool:
    """Whether a glob of ``rule``'s fields matches ``name``."""
    return rule.fields is not None and any(fnmatch.fnmatchcase(name, glob) for glob in rule.fields)


def _wild(chunk: str) -> bool:
    """Whether a key's chunk matches more than one value: ``*``, ``**``, or one holding ``$*``."""
    return chunk in {'*', '**'} or '$*' in chunk


def _covers(rule: Rule, op: Op, contract: Guarded) -> bool:
    """Whether ``rule``'s ``ops`` name ``op`` and one of its ``operations`` globs matches ``contract``."""
    return op in rule.ops and any(
        fnmatch.fnmatchcase(contract.name, glob) or fnmatch.fnmatchcase(contract.qualname, glob)
        for glob in rule.operations
    )


def _tag(rule: Rule, tag: str) -> bool:
    """Whether a glob of ``rule``'s variants matches ``tag``."""
    return rule.variants is not None and any(fnmatch.fnmatchcase(tag, glob) for glob in rule.variants)


def _any_tag(rule: Rule, contract: Guarded) -> bool:
    """Whether ``rule`` covers some call on ``contract``: it names no variants, or one of ``contract``'s."""
    return rule.variants is None or (
        isinstance(contract, Contract) and any(_tag(rule, tag) for tag in contract.variants)
    )
