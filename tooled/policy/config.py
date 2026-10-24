"""The policy file: what it holds, where it's found, and how it loads.

A TOML file, loaded strict (``unknown='raise'`` throughout), so a misspelt key anywhere is refused rather than
silently weakening a rule:

```toml
[mesh]                  # zeared's own SessionConfig: the one mesh this policy reaches
mode = "client"
router = "tcp/127.0.0.1:7447"

[protocol]
packages = ["demo.protocol"]

[bind]                  # these slots render every key
world = "w1"

[inject.token]          # every request that declares `token` carries it
env = "DEMO_TOKEN"

[inject.order_id]       # made fresh for each call
generate = "uuid"

[[allow]]
ops = ["read", "query"]
keys = ["demo/w1/**"]

[[deny]]                # deny wins
ops = ["invoke"]
operations = ["MoveResult"]
variants = ["dev-*"]

[[deny]]                # hidden: out of tools and describe, refused in a call
ops = ["query"]
fields = ["ask_id"]
```

A rule's ``ops`` say what's done (``read``, ``query``, ``invoke``…), and its ``operations`` what it's done to: globs
over the names ``tooled operations`` lists. Nothing is allowed until a rule allows it. A deny rule with ``fields``
denies no call, only the optional request fields it names, wherever a call gives one. Where the file is found:
``--policy``, else ``$TOOLED_POLICY``, else ``./tooled.toml``. A relative path inside it (an inject source's file) is
read from the file's own directory.
"""

from __future__ import annotations

import enum
import os
import tomllib
from pathlib import Path

import seared as s
from zeared import KeyExprError, SessionConfig, keyexpr

from ..errors import PolicyFileError

#: The environment variable that names the policy file, when ``--policy`` doesn't.
POLICY_ENV = 'TOOLED_POLICY'

#: The policy file looked for in the working directory when nothing else names one.
DEFAULT_NAME = 'tooled.toml'


class Generator(enum.StrEnum):
    """What a generated value is made as."""

    UUID = 'uuid'  # a fresh uuid4, as 32 lowercase hex digits


class Op(enum.StrEnum):
    """What a rule allows or denies: what's done, one op per kind of contract."""

    READ = 'read'  # fetch and listen: a retained class, a stream, a presence feed
    QUERY = 'query'  # a queryable that declares EFFECT = READ
    INVOKE = 'invoke'  # any other queryable: WRITE, ACTION, or no EFFECT at all
    PUBLISH = 'publish'  # a raw send
    UNRETAIN = 'unretain'  # clearing a retained value


@s.seared(unknown='raise')
class Source(s.Seared):
    """Where an injected value comes from: exactly one of ``env``, ``file``, ``toml`` or ``generate``."""

    env: str | None = s.Str(default=None, doc='an environment variable holding the value')
    file: str | None = s.Str(default=None, doc='a file whose whole content, stripped, is the value')
    toml: str | None = s.Str(default=None, doc='a TOML file holding the value, found by `table`, `match` and `key`')
    table: str | None = s.Str(
        default=None, doc='with `toml`: an array of tables to pick one from; none reads `key` at the top level'
    )
    match: dict[str, str] = s.Str(
        keyed=True, default_factory=dict, doc="with `table`: the values the one table's keys must hold"
    )
    key: str | None = s.Str(default=None, doc='with `toml`: the key whose value is injected')
    generate: Generator | None = s.Enum(
        enum=Generator, default=None, doc='a value made fresh for each call: `uuid`, a uuid4 as 32 hex digits'
    )


@s.seared(unknown='raise')
class Rule(s.Seared):
    """One allow or deny rule. A call matches it when every part matches."""

    ops: list[Op] = s.Enum(enum=Op, many=True, required=True, doc="the ops it covers: what's done")
    operations: list[str] = s.Str(
        many=True, default_factory=lambda: ['*'], doc='globs over operation names, or `module:Class`: what to'
    )
    keys: list[str] = s.Str(many=True, default_factory=lambda: ['**'], doc='Zenoh key expressions')
    variants: list[str] | None = s.Str(
        many=True, default=None, doc="globs over the tags of a request's unions; none covers the whole contract"
    )
    fields: list[str] | None = s.Str(
        many=True,
        default=None,
        doc="on a deny rule, globs over a request's optional top-level fields, which it hides; none covers the call",
    )


@s.seared(unknown='raise')
class Protocol(s.Seared):
    """The protocol the policy reads."""

    packages: list[str] = s.Str(many=True, required=True, doc='the packages (or modules) whose contracts it reads')


@s.seared(unknown='raise')
class Policy(s.Seared):
    """A whole policy file."""

    mesh: SessionConfig = s.T(SessionConfig, required=True, doc='the one mesh this policy reaches')
    protocol: Protocol = s.T(Protocol, required=True, doc='the protocol it reads')
    bind: dict[str, str] = s.Str(keyed=True, default_factory=dict, doc='slot values every key renders with')
    inject: dict[str, Source] = s.T(
        Source, keyed=True, default_factory=dict, doc='request fields every request that declares them carries'
    )
    allow: list[Rule] = s.T(Rule, many=True, default_factory=list, doc='what may be done')
    deny: list[Rule] = s.T(Rule, many=True, default_factory=list, doc='what may not, whatever allows it')


#: Tables a later release reads, refused here with the reason rather than as an unknown key.
_LATER = {
    'keepalive': '`[[keepalive]]` runs in `tooled serve`, which this version of tooled does not have',
}
_LATER_PROTOCOL = {
    'extension': '`[protocol] extension` arrives with extensions, which this version of tooled does not have',
}

#: A rule's keys renamed, refused naming the new one rather than as an unknown key.
_RENAMED_RULE = {
    'contracts': '`contracts` was renamed `operations` in tooled 0.2.4; rename the key',
}


def find(given: str | None) -> Path:
    """The policy file: ``given`` (``--policy``), else ``$TOOLED_POLICY``, else ``./tooled.toml``.

    Raises:
        PolicyFileError: There's no file there.
    """
    if given:
        path, via = Path(given).expanduser(), '--policy'
    elif os.environ.get(POLICY_ENV):
        path, via = Path(os.environ[POLICY_ENV]).expanduser(), f'${POLICY_ENV}'
    else:
        path, via = Path.cwd() / DEFAULT_NAME, f'the working directory (no --policy, no ${POLICY_ENV})'
    if not path.is_file():
        msg = f'no policy file at {path}, from {via}'
        raise PolicyFileError(msg)
    return path.resolve()


def load(path: Path) -> Policy:
    """Read and check the policy file at ``path``.

    Raises:
        PolicyFileError: It isn't TOML, or doesn't load: an unknown key (a renamed one named), a wrong type, a
            missing table, a rule with no ops, ``fields`` on an allow rule or naming none, a key that isn't a key
            expression, a bound value that isn't one chunk, or an inject source that isn't exactly one source.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding='utf-8'))
    except OSError as exc:
        msg = f'could not read {path}: {exc}'
        raise PolicyFileError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f'{path} is not valid TOML: {exc}'
        raise PolicyFileError(msg) from exc
    _refuse_unread(raw)
    try:
        policy = Policy.load(raw)
    except s.ValidationError as exc:
        raise PolicyFileError(str(exc), path=exc.path) from exc
    _check(policy)
    return policy


def _refuse_unread(raw: dict[str, object]) -> None:
    """Refuse a key this version doesn't read, saying why: a later release's table, or a rule's renamed key."""
    for table, why in _LATER.items():
        if table in raw:
            raise PolicyFileError(why, path=(table,))
    protocol = raw.get('protocol')
    for key, why in _LATER_PROTOCOL.items():
        if isinstance(protocol, dict) and key in protocol:
            raise PolicyFileError(why, path=('protocol', key))
    for side in ('allow', 'deny'):
        rules = raw.get(side)
        for index, rule in enumerate(rules if isinstance(rules, list) else ()):
            for key, why in _RENAMED_RULE.items():
                if isinstance(rule, dict) and key in rule:
                    raise PolicyFileError(why, path=(side, index, key))


def _check(policy: Policy) -> None:
    """What the classes can't say: rules with ops and real keys, bound chunks, single sources."""
    if not policy.protocol.packages:
        msg = '`[protocol] packages` names no package'
        raise PolicyFileError(msg, path=('protocol', 'packages'))
    for side in ('allow', 'deny'):
        for index, rule in enumerate(getattr(policy, side)):
            _check_rule(rule, side, (side, index))
    for slot, value in policy.bind.items():
        if not value or '/' in value or not keyexpr.is_concrete(value):
            msg = f'a bound slot is one chunk with no wildcard, not {value!r}'
            raise PolicyFileError(msg, path=('bind', slot))
    for name, source in policy.inject.items():
        _check_source(source, ('inject', name))


def _check_rule(rule: Rule, side: str, where: tuple[str, int]) -> None:
    """One rule: an op at least, real key expressions, and ``fields`` only on a deny rule, naming one at least."""
    if not rule.ops:
        msg = 'a rule must name at least one op'
        raise PolicyFileError(msg, path=(*where, 'ops'))
    for at, key in enumerate(rule.keys):
        try:
            keyexpr.includes(key, key)
        except KeyExprError as exc:
            raise PolicyFileError(str(exc), path=(*where, 'keys', at)) from exc
    if rule.fields is not None and side == 'allow':
        msg = '`fields` hides request fields, which only a deny rule does; move it to a [[deny]] rule'
        raise PolicyFileError(msg, path=(*where, 'fields'))
    if rule.fields == []:
        msg = '`fields` names no field, so the rule would hide nothing; name one, or leave `fields` out'
        raise PolicyFileError(msg, path=(*where, 'fields'))


def _check_source(source: Source, where: tuple[str, ...]) -> None:
    from .sources import path_problem

    given = [kind for kind in ('env', 'file', 'toml', 'generate') if getattr(source, kind) is not None]
    if len(given) != 1:
        msg = f'an inject source is exactly one of env, file, toml or generate, not {given or "none"}'
        raise PolicyFileError(msg, path=where)
    for kind in ('file', 'toml'):  # a path's variables are checked as written: no environment is read at load
        text = getattr(source, kind)
        problem = path_problem(text) if text is not None else None
        if problem is not None:
            raise PolicyFileError(problem, path=(*where, kind))
    if source.toml is None:
        extra = [kind for kind in ('table', 'key') if getattr(source, kind) is not None]
        if source.match:
            extra.append('match')
        if extra:
            msg = f'{", ".join(extra)} go with toml, not {given[0]}'
            raise PolicyFileError(msg, path=where)
        return
    if source.key is None:
        msg = 'a toml source names the `key` to read'
        raise PolicyFileError(msg, path=(*where, 'key'))
    if source.match and source.table is None:
        msg = '`match` picks a table, so it needs `table`'
        raise PolicyFileError(msg, path=(*where, 'match'))
