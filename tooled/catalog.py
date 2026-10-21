"""The catalog: the contracts a protocol defines, as tooled sorts and names them.

A contract is a zeared ``Message`` class. Each one is read once, with zeared's and seared's own introspection,
into a :class:`Contract`: its kind, its topic templates and their slots, its request class, its declared effect,
and the tags of its request's unions. Request and view classes aren't contracts; they're reached through one.

**Kinds:**
- ``retained``: ``RETAINED = True``, whether or not it also declares ``LIVELINESS``;
- ``queryable``: it has a ``REQUEST``, or declares an ``EFFECT``;
- ``liveliness``: ``LIVELINESS`` alone, a presence feed;
- ``stream``: anything else, a plain publish-subscribe topic.

**Names:** a contract is named by its class's name (``FleetState``), and always also answers to
``module:Class``. When two packages define classes with the same name, the bare name is ambiguous: it's refused
wherever it's used, naming both, and the catalog lists each by ``module:Class``.
"""

from __future__ import annotations

import enum
import fnmatch
import importlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from seared import FieldKind
from seared.doc import collect, introspect, is_seared_class
from zeared.doc import introspect_message, is_message_class

from .errors import CatalogError, UsageError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from seared import Seared
    from zeared import Effect, Message
    from zeared.doc import SlotDoc

#: A slot in a topic template: ``{name}``, or a trailing ``{name**}`` (zeared's template grammar).
SLOT = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)(\*\*)?\}')


class Kind(enum.StrEnum):
    """What a contract is on the mesh, which decides what can be done with it."""

    RETAINED = 'retained'
    QUERYABLE = 'queryable'
    LIVELINESS = 'liveliness'
    STREAM = 'stream'


@dataclass(frozen=True, slots=True)
class Contract:
    """One contract, as the catalog reads it.

    Attributes:
        name: The class's name.
        qualname: ``module:Class``, which always names it.
        cls: The ``Message`` class.
        kind: What it is on the mesh.
        templates: Its topic templates: ``TOPIC`` first, then any ``EXTRA_TOPICS``.
        slots: Every slot of every template, each once, in the order they first appear.
        request: Its ``REQUEST`` class, or ``None``.
        effect: The ``EFFECT`` it declares, or ``None`` when it declares none.
        variants: The tags of its request's unions (the fields at its request's root), in order; empty when it
            has none.
    """

    name: str
    qualname: str
    cls: type[Message]
    kind: Kind
    templates: tuple[str, ...]
    slots: tuple[SlotDoc, ...]
    request: type[Seared] | None
    effect: Effect | None
    variants: tuple[str, ...]

    @property
    def slot_names(self) -> tuple[str, ...]:
        """The names of its slots."""
        return tuple(slot.name for slot in self.slots)


class Catalog:
    """The contracts of one or more protocol packages.

    Args:
        classes: Any classes; the zeared ``Message`` classes among them become contracts.
    """

    def __init__(self, classes: Iterable[type]) -> None:
        found: dict[str, Contract] = {}
        for cls in classes:
            if is_message_class(cls):
                contract = read(cls)
                found.setdefault(contract.qualname, contract)
        self.contracts: tuple[Contract, ...] = tuple(sorted(found.values(), key=lambda c: (c.name, c.qualname)))
        self._by_name: dict[str, list[Contract]] = {}
        for contract in self.contracts:
            self._by_name.setdefault(contract.name, []).append(contract)

    @classmethod
    def load(cls, packages: Sequence[str]) -> Catalog:
        """The contracts every package (or module) defines, its submodules included.

        Raises:
            CatalogError: A package won't import.
        """
        classes: list[type] = []
        for index, package in enumerate(packages):
            try:
                importlib.import_module(package)
                classes += collect(package)
            except Exception as exc:  # whatever a package raises on import, it's this error
                msg = f'protocol package {package!r} would not import: {type(exc).__name__}: {exc}'
                raise CatalogError(msg, path=('protocol', 'packages', index)) from exc
        return cls(classes)

    def label(self, contract: Contract) -> str:
        """The name to show for ``contract``: its class's name, or ``module:Class`` when that's ambiguous."""
        return contract.name if len(self._by_name.get(contract.name, ())) == 1 else contract.qualname

    def get(self, name: str) -> Contract:
        """The contract ``name`` names: a class's name, or ``module:Class``.

        Raises:
            UsageError: It names none, or a class's name that two packages define.
        """
        if ':' in name:
            for contract in self.contracts:
                if contract.qualname == name:
                    return contract
        else:
            named = self._by_name.get(name, [])
            if len(named) == 1:
                return named[0]
            if named:
                both = ', '.join(c.qualname for c in named)
                msg = f'{name!r} is ambiguous: name one of {both}'
                raise UsageError(msg)
        msg = f'no contract named {name!r}'
        raise UsageError(msg)

    def matching(self, pattern: str) -> list[Contract]:
        """The contracts a glob matches, by class name or by ``module:Class``."""
        return [
            c
            for c in self.contracts
            if fnmatch.fnmatchcase(c.name, pattern) or fnmatch.fnmatchcase(c.qualname, pattern)
        ]

    @property
    def slot_names(self) -> frozenset[str]:
        """Every slot any contract's templates have."""
        return frozenset(name for c in self.contracts for name in c.slot_names)

    @property
    def request_fields(self) -> frozenset[str]:
        """Every field any contract's request class declares, by its wire name."""
        return frozenset(name for c in self.contracts if c.request is not None for name in wire_names(c.request))


def read(cls: type[Message]) -> Contract:
    """Read one ``Message`` class into a :class:`Contract`.

    Raises:
        CatalogError: Its ``EFFECT`` isn't an effect, or its ``REQUEST`` isn't a seared class.
    """
    try:
        doc = introspect_message(cls)  # reads EFFECT through zeared.effect_of, which checks it
    except (TypeError, ValueError) as exc:
        msg = f'{cls.__module__}:{cls.__name__}: {exc}'
        raise CatalogError(msg) from exc
    effect, request = doc.effect, doc.request
    if request is not None and not is_seared_class(request):
        msg = f'{cls.__module__}:{cls.__name__}: its REQUEST, {request!r}, is not a seared class'
        raise CatalogError(msg)
    if doc.retained:
        kind = Kind.RETAINED
    elif doc.request is not None or effect is not None:
        kind = Kind.QUERYABLE
    elif doc.liveliness:
        kind = Kind.LIVELINESS
    else:
        kind = Kind.STREAM
    return Contract(
        name=cls.__name__,
        qualname=f'{cls.__module__}:{cls.__name__}',
        cls=cls,
        kind=kind,
        templates=tuple(t for t in (doc.topic, *doc.extra_topics) if t),
        slots=doc.slots,
        request=request,
        effect=effect,
        variants=union_tags(request) if request is not None else (),
    )


def union_tags(cls: type) -> tuple[str, ...]:
    """The tags of every union at ``cls``'s root, in order, each once; none when it isn't a seared class."""
    if not is_seared_class(cls):
        return ()
    tags: dict[str, None] = {}
    for field in introspect(cls).fields:
        if field.kind is FieldKind.UNION:
            for variant in field.variants or ():
                tags.setdefault(variant.tag, None)
    return tuple(tags)


def wire_names(cls: type) -> tuple[str, ...]:
    """The wire names of ``cls``'s fields; none when it isn't a seared class.

    A flat union has no wire name of its own (its tag and its variant's fields sit beside the others), so it
    adds none.
    """
    if not is_seared_class(cls):
        return ()
    return tuple(
        field.wire_key or field.attr
        for field in introspect(cls).fields
        if not (field.kind is FieldKind.UNION and field.envelope == 'flat')
    )


def render(template: str, values: Mapping[str, str]) -> str:
    """A template as a key expression: each slot filled from ``values``, or widened when it has no value.

    A ``{name}`` with no value becomes ``*``, one chunk, and a trailing ``{name**}`` becomes ``*/**``, one chunk or
    more, as :func:`zeared.keyexpr.shape` reads them. Literal text, wildcards included, stays as it is.
    """

    def fill(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        if value is not None:
            return value
        return '*/**' if match.group(2) else '*'

    return SLOT.sub(fill, template)
