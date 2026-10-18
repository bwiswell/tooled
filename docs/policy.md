# The policy file

A policy says which mesh parsed reaches, which protocol it reads there, and what it may do. It is a TOML file,
loaded strict: a key the file format doesn't have, anywhere, is refused with its path, so a typo can't silently
weaken a rule.

parsed finds it at `--policy PATH` (given after the verb), else at `$PARSED_POLICY`, else at `./parsed.toml`.
`parsed check` loads it and reports what it shows.

The examples here read a small protocol, `demo.protocol`, on keys under `demo/{world}/…`:

| Contract | Kind | Topic |
|---|---|---|
| `Station` | retained | `demo/{world}/station/{station}` |
| `Status` | retained, and a presence feed | `demo/{world}/status` |
| `Notice` | stream | `demo/{world}/notice/{player}` |
| `LookReply` | queryable, `EFFECT = READ`; its request takes a `token` and a union of `ping`, `pong` and `dev-peek` | `demo/{world}/look/{player}` |
| `MoveResult` | queryable, `EFFECT = WRITE` | `demo/{world}/move/{player}` |

## A whole policy

```toml
[mesh]                          # zeared's own SessionConfig: the one mesh this policy reaches
mode = "client"
router = "tcp/127.0.0.1:7447"

[protocol]
packages = ["demo.protocol"]

[bind]                          # these slots render every key
world = "w1"

[inject.token]                  # every request that declares `token` carries it
toml = "~/.config/demo/identity.toml"
table = "players"
match = { world = "w1", player = "ann" }
key = "token"

[[allow]]
ops = ["read", "query"]
keys = ["demo/w1/**"]

[[deny]]                        # deny wins
ops = ["query"]
contracts = ["LookReply"]
variants = ["dev-*"]
```

## `[mesh]`

The session parsed opens: zeared's own `SessionConfig`, loaded by zeared, strict too. `mode` and `router` are the
usual pair for reaching a hub; `connect`, `listen`, `retry` and the rest are as zeared documents them. No flag
overrides it, so a policy can't be pointed at another mesh from the command line.

## `[protocol]`

| Key | Meaning |
|---|---|
| `packages` | The protocol packages (or modules) whose contracts parsed reads, submodules included. Each must import in parsed's environment, on the same zeared parsed runs on. |

Each zeared `Message` class becomes a **contract**, sorted by what it is:

| Kind | What makes it so | Operation |
|---|---|---|
| retained | `RETAINED = True`, presence feed or not | `read` |
| stream | none of the others | `read` |
| presence feed | `LIVELINESS` alone | `read` |
| queryable | a `REQUEST`, or a declared `EFFECT` | `query` when `EFFECT = READ`; `invoke` otherwise, no effect declared included |

A contract is named by its class's name, and always answers to `module:Class` too. When two packages define classes
by the same name, the bare name is ambiguous: wherever it's used, in a rule or on the command line, it is refused,
naming both, and `module:Class` picks one.

## `[bind]`

Slot values every key renders with. A bound slot is fixed: a call that gives it another value is refused, and
`describe` and `contracts` show it filled. Each value is one concrete chunk of a key: no `/`, and no wildcard.

Binding is a guardrail and a convenience. Where a server checks a token against a slot, binding that slot saves
giving it each time; a policy that wants other values of a slot (another player's public state, say) leaves it
unbound.

## `[inject]`

Request fields every request that declares them carries, each from a source:

| Source | Keys |
|---|---|
| An environment variable | `env = "NAME"` |
| A file's whole content, stripped | `file = "path"` |
| A value in a TOML file | `toml = "path"`, `key = "…"`; and, to pick one table of an array of tables, `table = "…"` with `match = { … }`, which must match exactly one |
| A value made fresh for each call | `generate = "uuid"`: a uuid4, as 32 lowercase hex digits |

- A relative path is read from the policy file's own directory.
- An injected field is taken out of the schema `describe` shows, and a request that gives a value for it is refused.
- A value read from a source is never printed: not in a result, an error, or `check`'s summary, which shows only
  its source and length.
- There is no literal source, so a secret can't end up in the policy file.
- **A generated value** is an id a server can tell a repeat by, such as an order's. It is no secret: a reply may
  echo it. `check` names its source and makes none.

```toml
[inject.order_id]               # every request that declares `order_id` carries a fresh one
generate = "uuid"
```

## Rules: `[[allow]]` and `[[deny]]`

| Key | Default | Meaning |
|---|---|---|
| `ops` | required | `read`, `query`, `invoke`, `publish`, `unretain`. This version acts on `read` and `query`; the others load, so a policy written now carries forward |
| `contracts` | `["*"]` | globs over contract names, or `module:Class` |
| `keys` | `["**"]` | Zenoh key expressions (`*` one chunk, `**` any number, `$*` within a chunk) |
| `variants` | none | globs over the tags of a request's root union; none covers the whole contract |

**Nothing is allowed until a rule allows it, and deny wins.** A rule covers a contract when it names the contract's
operation and one of its globs matches the contract's name. Then its keys decide, with Zenoh's own matching:

- **What's shown.** A contract is shown when an allow rule's keys intersect one of its topics (as `[bind]` renders
  it) and no deny rule covers all of them. `contracts`, `describe` and every read see only what's shown.
- **What a fetch or a query may reach.** A call renders one selector: the topic with its given and bound slots
  filled, the rest widened. It is allowed when an allow rule's keys include the whole selector, and no deny rule's
  keys intersect it. Replies carry no keys to filter by afterwards, so a selector that could reach a denied key is
  refused outright, with a hint to narrow the slots.
- **What a stream hands on.** `listen` checks each sample's key, which it does carry: a sample on a denied key is
  left out and counted, rather than the whole stream refused.

**Variants.** A rule with `variants` covers only the request-union tags its globs match. A deny rule with variants
refuses those tags, not the contract, and `describe` leaves them out of the request's schema. An allow rule with
variants allows only those tags.

**Names must name something.** A contract name, a variant tag, a bound slot or an injected field written without a
glob must exist in the protocol, so a typo is refused at load. A glob may match nothing.

## Not in this version

`[[keepalive]]` and `[protocol] extension` are refused with the reason: they arrive with `parsed serve` and with
extensions.
