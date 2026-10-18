"""The executor: the session a command opens, and the reads and writes it makes on it.

Every call follows the same steps. The slots given are checked against the contract's, and merged with the ones
the policy binds. The selector they render is checked against the policy, before anything reaches the mesh. Then
zeared does the asking, and each reply is dumped as seared's JSON form.

- **fetch** reads a retained contract. With slots to narrow by, it's a query on the retention queryables, which
  answer whatever intersects the selector; with none, and several topics, it's ``fetch_retained`` across them all.
  The set comes back sorted, since retained values have no order of their own.
- **query** asks a queryable that declares ``EFFECT = READ``. The request is the given JSON, with the policy's
  injected fields added, loaded strict by its own class, so seared's checks (and any ``__post_init__``) run before
  the query is sent. Replies come back in the order they arrived.
- **invoke** asks a queryable that writes, the same way, at one key: every slot given or bound, and none a
  wildcard. A server's refusal is a reply, not an error. ``prepare`` builds and checks a call without sending it,
  which is what a dry run prints (``preview``), injected secrets masked.
- **listen** subscribes to a retained contract or a stream, and hands on each value as it arrives: a retained
  contract's current values first (``origin`` ``replay``), then live ones. Each sample's key is checked against the
  slots asked for and against the policy, so a sample on a denied key is left out rather than the whole stream
  refused. It stops after a count, after a duration, or when told to.

A reply that won't decode is left out, and noted. So is an empty answer to a query, a sample on a denied key, and
a sample the stream dropped because the reader fell behind.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import seared
from seared import FieldKind
from seared.doc import introspect, is_seared_class
from zeared import KeyExprError, keyexpr

from .catalog import render
from .errors import InvalidRequestError, MeshError, RefusedError, UsageError
from .output import dump
from .policy import Op, op_of

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

    from seared import Seared
    from zeared import SessionConfig

    from .catalog import Contract
    from .policy import Rules

#: Zenoh appends where in its source it refused; the reason is what's useful.
_SOURCE = re.compile(r'\s+at /\S+$')

#: What a dry run shows in place of an injected secret.
HIDDEN = '<injected>'

#: zeared's ``query`` takes slots as keyword arguments beside these, so a slot by one of these names can't be given.
_QUERY_ARGS = frozenset({'session', 'params', 'request', 'timeout', 'target', 'consolidation', 'on_error'})


@dataclass
class Result:
    """What a read gives: the replies, dumped, and notes on what was left out or missing."""

    items: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def open_session(config: SessionConfig) -> Any:  # a zenoh.Session, or a ManagedSession
    """Open the session the policy's ``[mesh]`` describes.

    Raises:
        MeshError: It wouldn't open, a router unreachable say. The message is zenoh's reason.
    """
    import zeared

    try:
        return zeared.open(config)
    except Exception as exc:  # zenoh raises its own error type, which parsed doesn't import
        msg = f'the session would not open: {_reason(exc)}'
        raise MeshError(msg) from exc


def slot_values(contract: Contract, rules: Rules, given: Mapping[str, str]) -> dict[str, str]:
    """The slots a call renders with: the ones given, checked, and the ones the policy binds.

    Raises:
        UsageError: A slot the contract doesn't have, or a value that isn't one chunk (several, for a trailing
            ``{name**}`` slot).
        RefusedError: A bound slot given another value.
    """
    label, slots = rules.catalog.label(contract), {slot.name: slot for slot in contract.slots}
    bound = rules.bound(contract)
    for name, value in given.items():
        if name not in slots:
            msg = f'{label} has no slot {name!r}; its slots: {", ".join(slots) or "none"}'
            raise UsageError(msg)
        if name in bound and value != bound[name]:
            msg = f'the policy binds {name!r} to {bound[name]!r}'
            raise RefusedError(msg)
        if not value or ('/' in value and not slots[name].multi) or value.startswith('/') or value.endswith('/'):
            msg = f'slot {name!r} takes one chunk, not {value!r}'
            raise UsageError(msg)
    values = {**given, **bound}
    clash = sorted(_QUERY_ARGS & values.keys())
    if clash:
        msg = f'{label} has a slot named {clash[0]!r}, which zeared reads as its own argument; it cannot be narrowed by'
        raise UsageError(msg)
    return values


def selector(template: str, values: Mapping[str, str]) -> str:
    """``template`` rendered with ``values``, checked as a key expression.

    Raises:
        UsageError: A value makes it no key expression.
    """
    key = render(template, values)
    try:
        keyexpr.includes(key, key)
    except KeyExprError as exc:
        msg = f'{key} is no key expression: {exc}'
        raise UsageError(msg) from exc
    return key


def fetch(session: Any, contract: Contract, rules: Rules, values: Mapping[str, str], *, timeout: float) -> Result:
    """Read a retained contract's values: those the slots select, or every one.

    Raises:
        RefusedError: The policy refuses a selector the read would reach.
        MeshError: The read failed.
    """
    result = Result()
    on_error = _collect(result, contract)
    cls = contract.cls
    if values or len(contract.templates) == 1:
        rules.check(contract, selector(contract.templates[0], values))
        slots: dict[str, Any] = dict(values)
        items = _read(lambda: cls.query(session=session, timeout=timeout, on_error=on_error, **slots))
    else:
        for template in contract.templates:
            rules.check(contract, selector(template, {}))
        items = _read(lambda: cls.fetch_retained(session=session, on_error=on_error))
    dumps = [dump(cls, item) for item in items]
    result.items = sorted(dumps, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return result


@dataclass
class Call:
    """A call built and checked, before anything is sent: its slots, the selector they render, and its request."""

    values: Mapping[str, str]
    selector: str
    request: Any = None  # the request, loaded; None for a contract that takes none
    variant: str | None = None


def prepare(
    contract: Contract,
    rules: Rules,
    values: Mapping[str, str],
    *,
    request: Mapping[str, Any] | None,
    injected: Mapping[str, str],
) -> Call:
    """A call on a queryable, built from ``request`` and the policy's injected fields, and checked against the policy.

    A write reaches one key: on a contract whose operation is ``invoke``, every slot must be given or bound, and
    none may hold a wildcard, so a write can't fan out to every queryable a widened selector would reach.

    Raises:
        UsageError: A request given to a contract that takes none; or a write whose selector isn't one key.
        RefusedError: A value given for an injected field; or the policy refuses the selector, or the request's
            variant.
        InvalidRequestError: The request doesn't load as its class.
    """
    built, variant = None, None
    if contract.request is None:
        if request is not None:
            msg = f'{rules.catalog.label(contract)} takes no request'
            raise UsageError(msg)
    else:
        built = build_request(contract.request, request or {}, injected)
        variant = variant_of(contract, built)
    key = selector(contract.templates[0], values)
    if op_of(contract) is Op.INVOKE and not keyexpr.is_concrete(key):
        missing = [slot.name for slot in contract.slots if slot.name not in values]
        hint = f'give {", ".join(missing)}' if missing else 'give each slot a value without a wildcard'
        msg = f'a write reaches one key, and {key} could reach more: {hint}'
        raise UsageError(msg)
    rules.check(contract, key, variant=variant)
    return Call(values, key, built, variant)


def query(  # noqa: PLR0913 - one read: the contract, its slots, its request and the policy's part in it
    session: Any,  # a zenoh.Session, or a ManagedSession
    contract: Contract,
    rules: Rules,
    values: Mapping[str, str],
    *,
    request: Mapping[str, Any] | None,
    injected: Mapping[str, str],
    params: Mapping[str, str],
    timeout: float,
) -> Result:
    """Ask a read queryable, with a request built from ``request`` and the policy's injected fields.

    Raises:
        UsageError: A request given to a contract that takes none.
        RefusedError: A value given for an injected field; or the policy refuses the selector, or the request's
            variant.
        InvalidRequestError: The request doesn't load as its class.
        MeshError: The query failed.
    """
    call = prepare(contract, rules, values, request=request, injected=injected)
    return _ask(session, contract, call, params=params, timeout=timeout, what='read')


def invoke(  # noqa: PLR0913 - one write: the contract, its slots, its request and the policy's part in it
    session: Any,  # a zenoh.Session, or a ManagedSession
    contract: Contract,
    rules: Rules,
    values: Mapping[str, str],
    *,
    request: Mapping[str, Any] | None,
    injected: Mapping[str, str],
    timeout: float,
) -> Result:
    """Ask a queryable that writes, with a request built from ``request`` and the policy's injected fields.

    A refusal the server sends back is a reply like any other: what it says is the protocol's business.

    Raises:
        UsageError: A request given to a contract that takes none.
        RefusedError: A value given for an injected field; or the policy refuses the selector, or the request's
            variant.
        InvalidRequestError: The request doesn't load as its class.
        MeshError: The write failed.
    """
    call = prepare(contract, rules, values, request=request, injected=injected)
    return _ask(session, contract, call, params={}, timeout=timeout, what='write')


def preview(contract: Contract, call: Call, *, hidden: Collection[str]) -> dict[str, Any]:
    """What a dry run prints: the selector, the variant, and the request's wire form, ``hidden`` fields masked."""
    request = None
    if contract.request is not None and call.request is not None:
        request = dump(contract.request, call.request)
        for name in hidden:
            if name in request:
                request[name] = HIDDEN
    return {'selector': call.selector, 'variant': call.variant, 'request': request}


def _ask(  # noqa: PLR0913 - a prepared call, and how to send it
    session: Any,  # a zenoh.Session, or a ManagedSession
    contract: Contract,
    call: Call,
    *,
    params: Mapping[str, str],
    timeout: float,
    what: str,
) -> Result:
    """Send a prepared call, and gather its replies in the order they arrived; ``what`` it is names a failure."""
    result = Result()
    on_error = _collect(result, contract)
    slots: dict[str, Any] = dict(call.values)
    items = _read(
        lambda: contract.cls.query(
            session=session,
            request=call.request,
            params=dict(params) or None,
            timeout=timeout,
            on_error=on_error,
            **slots,
        ),
        what=what,
    )
    result.items = [dump(contract.cls, item) for item in items]
    if not items:
        result.notes.append(f'no replies within {timeout:g}s')
    return result


async def listen(  # noqa: PLR0913 - one subscription: the contract, its slots, and when to stop
    session: Any,  # a zenoh.Session, or a ManagedSession
    contract: Contract,
    rules: Rules,
    values: Mapping[str, str],
    *,
    each: Callable[[Any], None],
    meta: bool = False,
    count: int | None = None,
    duration: float | None = None,
    stop: asyncio.Event | None = None,
) -> Result:
    """Subscribe to ``contract`` and hand each value the slots and the policy allow to ``each``, as it arrives.

    Args:
        session: The session to subscribe on.
        contract: A retained contract, or a stream.
        rules: The policy's rules: each sample's key must be one they allow.
        values: The slots asked for, the bound ones included.
        each: Called with each value, or with ``{"key", "origin", "at", "value"}`` when ``meta`` is set.
        meta: Whether to hand on each sample's key, origin and time with its value.
        count: Stop after this many values.
        duration: Stop after this many seconds.
        stop: Stop when this is set: on a signal, say.

    Returns:
        Notes on what was left out: samples on denied keys, and samples the stream dropped.
    """
    selectors = [selector(template, values) for template in contract.templates]
    tally = _Tally()
    stream = contract.cls.alisten(session=session, meta=True)

    async def pump() -> None:
        async for message, sample in stream:
            if not _admit(contract, rules, selectors, sample.key_expr, tally):
                continue
            each(_shape(contract, message, sample, meta=meta))
            tally.handed += 1
            if count is not None and tally.handed >= count:
                return

    async with stream:
        await _until(pump(), stop=stop, duration=duration)
        result = Result()
        if tally.denied:
            result.notes.append(f'{tally.denied} samples on keys the policy denies were left out')
        if stream.dropped:
            result.notes.append(f'{stream.dropped} samples were dropped: the reader fell behind')
    return result


@dataclass
class _Tally:
    handed: int = 0
    denied: int = 0


def _admit(contract: Contract, rules: Rules, selectors: list[str], key: str, tally: _Tally) -> bool:
    """Whether a sample on ``key`` is one the slots asked for and the policy allows; a denied one is counted."""
    if not any(keyexpr.includes(chosen, key) for chosen in selectors):
        return False
    try:
        rules.check(contract, key)
    except RefusedError:
        tally.denied += 1
        return False
    return True


def _shape(contract: Contract, message: Any, sample: Any, *, meta: bool) -> Any:  # JSON-ready
    """A sample as ``listen`` hands it on: the value, or the value with its key, origin and time."""
    value = dump(contract.cls, message)
    if not meta:
        return value
    at = sample.issued_at.isoformat() if sample.issued_at is not None else None
    return {'key': sample.key_expr, 'origin': sample.origin.value, 'at': at, 'value': value}


async def _until(work: Any, *, stop: asyncio.Event | None, duration: float | None) -> None:  # a coroutine
    """Run ``work`` until it ends, ``stop`` is set, or ``duration`` passes; what it raised is raised again."""
    task = asyncio.create_task(work)
    waits = {task}
    if stop is not None:
        waits.add(asyncio.create_task(stop.wait()))
    _done, pending = await asyncio.wait(waits, timeout=duration, return_when=asyncio.FIRST_COMPLETED)
    for waiting in pending:
        waiting.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if task.done() and not task.cancelled() and task.exception() is not None:
        await task


def build_request(cls: type[Seared], given: Mapping[str, Any], injected: Mapping[str, str]) -> Any:
    """``cls`` loaded strict from ``given`` and the injected fields.

    Raises:
        RefusedError: ``given`` holds an injected field.
        InvalidRequestError: It doesn't load. The message is seared's, unless it's about an injected field, whose
            value it would show.
    """
    for name in injected:
        if name in given:
            msg = f'{name!r} is injected by the policy; leave it out of the request'
            raise RefusedError(msg)
    try:
        return cls.load({**given, **injected}, unknown='raise')
    except seared.ValidationError as exc:
        if exc.path and exc.path[0] in injected:
            msg = f'{cls.__name__}.{exc.path[0]}: the value the policy injects does not load'
            raise InvalidRequestError(msg, path=exc.path[:1]) from exc
        raise InvalidRequestError(str(exc), path=exc.path) from exc


def variant_of(contract: Contract, request: Any) -> str | None:  # the request instance
    """The tag of the variant ``request`` carries in its root union, or ``None`` when it has no union."""
    if contract.request is None or not is_seared_class(contract.request):
        return None
    for field_doc in introspect(contract.request).fields:
        if field_doc.kind is not FieldKind.UNION:
            continue
        value = getattr(request, field_doc.attr, None)
        for variant in field_doc.variants or ():
            if value is not None and type(value) is variant.cls:
                return variant.tag
    return None


def _read(call: Any, *, what: str = 'read') -> list[Any]:  # a zeared call, deferred
    try:
        return list(call())
    except Exception as exc:  # zenoh and zeared raise their own types; any of them is the mesh failing
        msg = f'the {what} failed: {_reason(exc)}'
        raise MeshError(msg) from exc


def _collect(result: Result, contract: Contract) -> Any:  # zeared's on_error callback
    def on_error(exc: Exception, _raw: bytes) -> None:
        result.notes.append(f'a reply would not decode as {contract.name}: {type(exc).__name__}: {_reason(exc)}')

    return on_error


def _reason(exc: BaseException) -> str:
    return _SOURCE.sub('', str(exc)).rstrip('. ')
