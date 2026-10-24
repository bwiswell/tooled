"""The catalog: the operations tooled lists, names and looks up: a protocol's contracts, and a tree's commands.

An operation is anything tooled can list, describe, guard or carry out (:class:`Operation`). A contract is one, and a
command is another; the catalog's lookups, listing and labels serve any operation alike.

A contract is a zeared ``Message`` class. Each one is read once, with zeared's and seared's own introspection,
into a :class:`Contract`: its kind, its topic templates and their slots, its request class, its declared effect,
and the tags of its request's unions. Request and view classes aren't contracts; they're reached through one.

**Kinds:**
- ``retained``: ``RETAINED = True``, whether or not it also declares ``LIVELINESS``;
- ``queryable``: it has a ``REQUEST``, or declares an ``EFFECT``;
- ``liveliness``: ``LIVELINESS`` alone, a presence feed;
- ``stream``: anything else, a plain publish-subscribe topic.

**Commands** are parsed's: a command class, read with ``parsed.doc`` into a :class:`Command`, under the prefix of
the tree that holds it. They come from an app's mounts (``[app] factory``, a function returning parsed's ``App``),
each mount's prefix leading its commands, so one package mounted twice gives each command twice, once a mount; or,
for a policy that only describes, from packages read as trees at the root (``[protocol] trees``).

**Overlaps:** a command that takes its fields from a contract's request class (it inherits it), or whose ``REPLY`` is a
contract's class, does what that contract does: :meth:`Catalog.links` lists each such pair, for a policy to show one
side of.

**Names:** an operation is named by its class's name (``FleetState``), and always also answers to
``module:Class``. A command under a prefix is led by its words, slashed: ``relay/Run``, ``relay/pkg.service:Run``,
and so are its keys (``relay/run``). When two operations share a name, the bare name is ambiguous: it's refused
wherever it's used, naming both, and the catalog lists each by ``module:Class``.
"""

from __future__ import annotations

import enum
import fnmatch
import importlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from parsed import App, DeclarationError, Tree
from parsed.doc import introspect_command
from seared import FieldKind
from seared.doc import collect, introspect, is_seared_class
from zeared import Effect
from zeared.doc import introspect_message, is_message_class

from .errors import CatalogError, UsageError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from parsed import Command as CommandClass
    from parsed.doc import CommandDoc
    from seared import Seared
    from zeared import Message
    from zeared.doc import SlotDoc

#: A slot in a topic template: ``{name}``, or a trailing ``{name**}`` (zeared's template grammar).
SLOT = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)(\*\*)?\}')


class Kind(enum.StrEnum):
    """What a contract is on the mesh, which decides what can be done with it."""

    RETAINED = 'retained'
    QUERYABLE = 'queryable'
    LIVELINESS = 'liveliness'
    STREAM = 'stream'


class Operation(Protocol):
    """What the catalog holds: anything tooled can list, describe, guard or carry out, named as a class is."""

    @property
    def name(self) -> str:
        """The class's name."""

    @property
    def qualname(self) -> str:
        """``module:Class``, which always names it."""


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


@dataclass(frozen=True, slots=True)
class Command:
    """One command of a tree, as the catalog reads it: a parsed command class, under its tree's prefix.

    Attributes:
        name: The class's name, led by the prefix's words, slashed: ``FleetMine``, ``relay/Run``.
        qualname: ``module:Class``, led the same way, which always names it.
        cls: The command class.
        under: The prefix, its words separated by single spaces; ``''`` at the root.
        keys: Each phrase's key template, led by the prefix: ``fleet/{fleet}/mine``, ``relay/run``.
        doc: parsed's ``CommandDoc`` of it: its phrases, slots, flags, kinds, asking first, reply and the rest.
        effect: The ``EFFECT`` it declares, in zeared's terms, or ``None`` when it declares none.
    """

    name: str
    qualname: str
    cls: type[CommandClass]
    under: str
    keys: tuple[str, ...]
    doc: CommandDoc
    effect: Effect | None

    @property
    def slot_names(self) -> tuple[str, ...]:
        """The names of its phrases' slots."""
        return tuple(slot.name for slot in self.doc.slots)


@dataclass(frozen=True, slots=True)
class Link:
    """A command and a contract that do the same thing.

    Attributes:
        command: The command.
        contract: The contract it overlaps.
        by: How it does: ``request``, when it takes its fields from the contract's request class, and ``reply``,
            when its ``REPLY`` is the contract's class; one or both, in that order.
    """

    command: Command
    contract: Contract
    by: tuple[str, ...]


class Catalog:
    """Operations, sorted and named: each once, by ``module:Class``.

    Args:
        operations: The operations; one whose ``module:Class`` comes again is kept once, the first.
    """

    def __init__(self, operations: Iterable[Operation]) -> None:
        found: dict[str, Operation] = {}
        for operation in operations:
            found.setdefault(operation.qualname, operation)
        self.operations: tuple[Operation, ...] = tuple(sorted(found.values(), key=lambda o: (o.name, o.qualname)))
        self.contracts: tuple[Contract, ...] = tuple(o for o in self.operations if isinstance(o, Contract))
        self.commands: tuple[Command, ...] = tuple(o for o in self.operations if isinstance(o, Command))
        self._by_name: dict[str, list[Operation]] = {}
        for operation in self.operations:
            self._by_name.setdefault(operation.name, []).append(operation)

    @classmethod
    def of(cls, classes: Iterable[type]) -> Catalog:
        """The contracts among ``classes``: the zeared ``Message`` classes, each read into a :class:`Contract`."""
        return cls(read(c) for c in classes if is_message_class(c))

    @classmethod
    def load(cls, packages: Sequence[str], *, trees: Sequence[str] = (), factory: str | None = None) -> Catalog:
        """The contracts every package (or module) defines, its submodules included, and the commands of the trees.

        Args:
            packages: The protocol's packages, whose contracts it reads.
            trees: Packages (or modules) whose commands it reads, each a tree at the root.
            factory: ``pkg.module:function``, returning the parsed ``App`` whose mounts hold the commands.

        Raises:
            CatalogError: A package won't import, a tree's commands don't build one, or the factory gives no app.
        """
        classes: list[type] = []
        for index, package in enumerate(packages):
            try:
                importlib.import_module(package)
                classes += collect(package)
            except Exception as exc:  # whatever a package raises on import, it's this error
                msg = f'protocol package {package!r} would not import: {type(exc).__name__}: {exc}'
                raise CatalogError(msg, path=('protocol', 'packages', index)) from exc
        commands: list[Command] = []
        for index, package in enumerate(trees):
            try:
                tree = Tree.collect(package)
            except Exception as exc:  # an import's error, or the tree's own
                msg = f'command tree {package!r} would not load: {type(exc).__name__}: {exc}'
                raise CatalogError(msg, path=('protocol', 'trees', index)) from exc
            commands += commands_of(tree)
        if factory is not None:
            for mount in made_app(factory).mounts:
                commands += commands_of(mount.tree)
        return cls([*(read(c) for c in classes if is_message_class(c)), *commands])

    def label(self, operation: Operation) -> str:
        """The name to show for ``operation``: its class's name, or ``module:Class`` when that's ambiguous."""
        return operation.name if len(self._by_name.get(operation.name, ())) == 1 else operation.qualname

    def get(self, name: str) -> Operation:
        """The operation ``name`` names: a class's name, or ``module:Class``.

        Raises:
            UsageError: It names none, or a class's name that two packages define.
        """
        if ':' in name:
            for operation in self.operations:
                if operation.qualname == name:
                    return operation
        else:
            named = self._by_name.get(name, [])
            if len(named) == 1:
                return named[0]
            if named:
                both = ', '.join(o.qualname for o in named)
                msg = f'{name!r} is ambiguous: name one of {both}'
                raise UsageError(msg)
        msg = f'no operation named {name!r}'
        raise UsageError(msg)

    def contract(self, name: str) -> Contract:
        """The contract ``name`` names, as :meth:`get` finds it.

        Raises:
            UsageError: It names no operation, an ambiguous one, or one that isn't a contract.
        """
        found = self.get(name)
        if not isinstance(found, Contract):
            msg = f'{self.label(found)} is not a contract'
            raise UsageError(msg)
        return found

    def matching(self, pattern: str) -> list[Operation]:
        """The operations a glob matches, by class name or by ``module:Class``."""
        return [
            o
            for o in self.operations
            if fnmatch.fnmatchcase(o.name, pattern) or fnmatch.fnmatchcase(o.qualname, pattern)
        ]

    @property
    def links(self) -> tuple[Link, ...]:
        """Each command and contract that overlap, in the catalog's order: the command's, then the contract's."""
        found = []
        for command in self.commands:
            for contract in self.contracts:
                by = tuple(
                    why
                    for why, holds in (
                        ('request', contract.request is not None and contract.request in command.doc.fields_from),
                        ('reply', command.doc.reply is contract.cls),
                    )
                    if holds
                )
                if by:
                    found.append(Link(command, contract, by))
        return tuple(found)

    @property
    def slot_names(self) -> frozenset[str]:
        """Every slot any contract's templates or any command's phrases have."""
        return frozenset(name for o in (*self.contracts, *self.commands) for name in o.slot_names)

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


def commands_of(tree: Tree) -> list[Command]:
    """Each command of ``tree``, read into a :class:`Command` under the tree's prefix.

    Raises:
        CatalogError: A command's declaration doesn't check.
    """
    lead = '/'.join(tree.under.split())
    found = []
    for declared in tree.commands:
        cls = declared.cls
        try:
            doc = introspect_command(cls)
        except (TypeError, DeclarationError) as exc:
            msg = f'{cls.__module__}:{cls.__name__}: {exc}'
            raise CatalogError(msg) from exc
        led = f'{lead}/' if lead else ''
        found.append(
            Command(
                name=f'{led}{cls.__name__}',
                qualname=f'{led}{cls.__module__}:{cls.__name__}',
                cls=cls,
                under=tree.under,
                keys=tuple('/'.join(part for part in (lead, key) if part) for key in doc.keys),
                doc=doc,
                effect=Effect(doc.effect.value) if doc.effect is not None else None,
            )
        )
    return found


def made_app(factory: str) -> App:
    """The parsed ``App`` that ``factory``, ``pkg.module:function``, returns when it's called with nothing.

    Raises:
        CatalogError: Its module won't import, it names nothing there or nothing callable, or what it returns
            isn't an app.
    """
    where = ('app', 'factory')
    module, _, name = factory.partition(':')
    try:
        found: Any = importlib.import_module(module)
        for part in name.split('.'):
            found = getattr(found, part)
    except Exception as exc:  # an import's error, or no such name
        msg = f'[app] factory {factory!r} would not load: {type(exc).__name__}: {exc}'
        raise CatalogError(msg, path=where) from exc
    if not callable(found):
        msg = f'[app] factory {factory!r} is no function, but {type(found).__name__}'
        raise CatalogError(msg, path=where)
    try:
        app = found()
    except Exception as exc:  # whatever building the app raises, it's this error
        msg = f'[app] factory {factory!r} failed: {type(exc).__name__}: {exc}'
        raise CatalogError(msg, path=where) from exc
    if not isinstance(app, App):
        msg = f"[app] factory {factory!r} returned {type(app).__name__}, not parsed's App"
        raise CatalogError(msg, path=where)
    return app


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


def top_fields(cls: type) -> dict[str | None, dict[str, bool]]:
    """The fields at the root of ``cls``'s wire form, by wire name, each with whether a load requires it.

    ``cls``'s own are under ``None``. A flat union's variant's fields sit at the root too, beside the others, so
    each variant's are under its tag; a nested one's sit under its payload key, which is one of ``cls``'s own. A
    union's tag is no field. None when ``cls`` isn't a seared class.
    """
    if not is_seared_class(cls):
        return {}
    out: dict[str | None, dict[str, bool]] = {None: {}}
    for field in introspect(cls).fields:
        if not (field.kind is FieldKind.UNION and field.envelope == 'flat'):
            out[None][field.wire_key or field.attr] = field.required
            continue
        for variant in field.variants or ():
            fields = introspect(variant.cls).fields if is_seared_class(variant.cls) else ()
            out.setdefault(variant.tag, {}).update({f.wire_key or f.attr: f.required for f in fields})
    return out


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
