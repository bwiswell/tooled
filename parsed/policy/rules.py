"""The policy's rules, checked against a catalog: which contracts it shows, and whether it allows a call.

Each contract takes one operation (:func:`op_of`). A rule matches a contract when it names that operation and one
of its globs matches the contract's name. Then its keys decide, with Zenoh's own matching (``zeared.keyexpr``):

- **What's shown.** A contract is shown when an allow rule's keys intersect one of its topics (as the policy's
  ``[bind]`` renders it) and no deny rule covers all of them.
- **What's allowed.** A call renders one selector: the topic with the given and bound slots filled, and the rest
  widened. It's allowed when an allow rule's keys include the whole selector and no deny rule's keys intersect
  it. Replies carry no keys to filter by afterwards, so a selector that could reach a denied key is refused
  outright: narrowing the slots is the way through.

**Deny wins.** **Variants:** a rule with ``variants`` covers only the request-union tags its globs match. A deny
rule with variants refuses those tags, not the contract; an allow rule with variants allows only those tags.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

from zeared import Effect, keyexpr

from ..catalog import Kind, render
from ..errors import PolicyFileError, RefusedError, UsageError
from .config import Op

if TYPE_CHECKING:
    from ..catalog import Catalog, Contract
    from .config import Policy, Rule

_GLOB = frozenset('*?[')


def op_of(contract: Contract) -> Op:
    """The operation ``contract`` takes.

    ``read`` for a retained class, a stream or a presence feed; ``query`` for a queryable that declares
    ``EFFECT = READ``; ``invoke`` for any other queryable, an undeclared one included, so the default fails safe.
    """
    if contract.kind is not Kind.QUERYABLE:
        return Op.READ
    return Op.QUERY if contract.effect is Effect.READ else Op.INVOKE


class Rules:
    """A policy's rules over a catalog.

    Args:
        policy: The loaded policy.
        catalog: The contracts of its protocol.

    Raises:
        PolicyFileError: A rule names a contract or a variant the protocol doesn't have, a bound slot is no
            contract's, or an injected field is no request's.
    """

    def __init__(self, policy: Policy, catalog: Catalog) -> None:
        self.policy = policy
        self.catalog = catalog
        self._check_references()

    # ---- what's shown --------------------------------------------------------------------------

    def bound(self, contract: Contract) -> dict[str, str]:
        """The bound slots ``contract``'s topics have."""
        return {slot: value for slot, value in self.policy.bind.items() if slot in contract.slot_names}

    def shapes(self, contract: Contract) -> tuple[str, ...]:
        """Every topic of ``contract`` as a key expression, its bound slots filled and the rest widened."""
        bound = self.bound(contract)
        return tuple(render(template, bound) for template in contract.templates)

    def visible(self, contract: Contract) -> bool:
        """Whether the policy shows ``contract`` at all."""
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
            and all(any(keyexpr.includes(key, shape) for key in rule.keys) for shape in shapes)
            for rule in self.policy.deny
        )
        return allowed and not hidden

    def visible_contracts(self) -> list[Contract]:
        """Every contract the policy shows, in the catalog's order."""
        return [contract for contract in self.catalog.contracts if self.visible(contract)]

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
                and _tag(rule, tag)
                and all(any(keyexpr.includes(key, shape) for key in rule.keys) for shape in shapes)
                for rule in self.policy.deny
            )
            if allowed and not denied:
                out.append(tag)
        return tuple(out)

    # ---- what's allowed ------------------------------------------------------------------------

    def check(self, contract: Contract, selector: str, *, variant: str | None = None) -> None:
        """Whether the policy allows a call on ``contract`` that renders ``selector``.

        Args:
            contract: The contract called.
            selector: The key expression the call reaches.
            variant: The tag of the request's union the call carries, if it carries one.

        Raises:
            RefusedError: A deny rule's keys intersect the selector (for its variant, if it names variants), or no
                allow rule's keys include it.
        """
        op, label = op_of(contract), self.catalog.label(contract)
        for index, rule in enumerate(self.policy.deny):
            if not _covers(rule, op, contract) or not any(keyexpr.intersects(key, selector) for key in rule.keys):
                continue
            if rule.variants is None:
                msg = f'{op} on {label} at {selector} is denied by deny[{index}]; narrow the slots if it reaches more'
                raise RefusedError(msg)
            if variant is not None and _tag(rule, variant):
                msg = f'{op} on {label} with variant {variant!r} is denied by deny[{index}]'
                raise RefusedError(msg)
        allowed = any(
            _covers(rule, op, contract)
            and (rule.variants is None or (variant is not None and _tag(rule, variant)))
            and any(keyexpr.includes(key, selector) for key in rule.keys)
            for rule in self.policy.allow
        )
        if not allowed:
            msg = f'no allow rule covers {op} on {label} at {selector}'
            raise RefusedError(msg)

    # ---- references --------------------------------------------------------------------------

    def _check_references(self) -> None:
        """A name without a glob must name something: a contract, a variant, a slot, a request field."""
        for side in ('allow', 'deny'):
            for index, rule in enumerate(getattr(self.policy, side)):
                self._check_rule(rule, (side, index))
        slots = self.catalog.slot_names
        for slot in self.policy.bind:
            if slot not in slots:
                msg = f'binds {slot!r}, which no contract has as a slot'
                raise PolicyFileError(msg, path=('bind', slot))
        fields = self.catalog.request_fields
        for name in self.policy.inject:
            if name not in fields:
                msg = f'injects {name!r}, which no request declares'
                raise PolicyFileError(msg, path=('inject', name))

    def _check_rule(self, rule: Rule, where: tuple[str | int, ...]) -> None:
        matched: dict[str, Contract] = {}
        for at, pattern in enumerate(rule.contracts):
            found = self.catalog.matching(pattern)
            if not (_GLOB & set(pattern)):
                try:
                    found = [self.catalog.get(pattern)]
                except UsageError as exc:
                    raise PolicyFileError(exc.message, path=(*where, 'contracts', at)) from exc
            matched |= {c.qualname: c for c in found}
        if rule.variants is None:
            return
        tags = {tag for contract in matched.values() for tag in contract.variants}
        for at, pattern in enumerate(rule.variants):
            if not (_GLOB & set(pattern)) and pattern not in tags:
                msg = f'variant {pattern!r} is no tag of the contracts this rule names'
                raise PolicyFileError(msg, path=(*where, 'variants', at))


def _covers(rule: Rule, op: Op, contract: Contract) -> bool:
    """Whether ``rule`` names ``op`` and a glob of it matches ``contract``."""
    return op in rule.ops and any(
        fnmatch.fnmatchcase(contract.name, glob) or fnmatch.fnmatchcase(contract.qualname, glob)
        for glob in rule.contracts
    )


def _tag(rule: Rule, tag: str) -> bool:
    """Whether a glob of ``rule``'s variants matches ``tag``."""
    return rule.variants is not None and any(fnmatch.fnmatchcase(tag, glob) for glob in rule.variants)


def _any_tag(rule: Rule, contract: Contract) -> bool:
    """Whether ``rule`` covers some call on ``contract``: it names no variants, or one of ``contract``'s."""
    return rule.variants is None or any(_tag(rule, tag) for tag in contract.variants)
