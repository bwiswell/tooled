# The policy file

A policy says which mesh tooled reaches, which protocol it reads there, and what it may do. It is a TOML file,
loaded strict: a key the file format doesn't have, anywhere, is refused with its path, so a typo can't silently
weaken a rule.

tooled finds it at `--policy PATH` (given after the verb), else at `$TOOLED_POLICY`, else at `./tooled.toml`.
`tooled check` loads it and reports what it shows.

The examples here read a small protocol, `demo.protocol`, on keys under `demo/{world}/…`:

| Contract | Kind | Topic |
|---|---|---|
| `Station` | retained | `demo/{world}/station/{station}` |
| `Status` | retained, and a presence feed | `demo/{world}/status` |
| `Notice` | stream | `demo/{world}/notice/{player}` |
| `LookReply` | queryable, `EFFECT = READ`; its request takes a `token` and a union of `ping`, `pong` and `dev-peek` | `demo/{world}/look/{player}` |
| `MoveResult` | queryable, `EFFECT = WRITE`; its request takes a `token`, a `move_id` and where to (`to`) | `demo/{world}/move/{player}` |

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

[inject.move_id]                # made fresh for each call: an id the server tells a repeat by
generate = "uuid"

[[allow]]
ops = ["read", "query"]
keys = ["demo/w1/**"]

[[allow]]                       # one write, for one player
ops = ["invoke"]
operations = ["MoveResult"]
keys = ["demo/w1/move/ann"]

[[deny]]                        # deny wins
ops = ["query"]
operations = ["LookReply"]
variants = ["dev-*"]

[[deny]]                        # hidden: out of tools and describe, refused in a call
ops = ["query"]
fields = ["ask_id"]
```

## `[mesh]`

The session tooled opens: zeared's own `SessionConfig`, loaded by zeared, strict too. `mode` and `router` are the
usual pair for reaching a hub; `connect`, `listen`, `retry` and the rest are as zeared documents them. No flag
overrides it, so a policy can't be pointed at another mesh from the command line.

**A policy of commands alone needs none.** With no `[protocol] packages`, only an app's commands or trees, `[mesh]`
may be left out: what an app's commands reach is the app's own business. Then no rule may name a wire op (`read`,
`query`, `invoke`, `publish`, `unretain`), since nothing reaches a wire, and `check` shows `"mesh": null`. A policy
with protocol packages needs its mesh.

## `[protocol]`

| Key | Meaning |
|---|---|
| `packages` | The protocol packages (or modules) whose contracts tooled reads, submodules included. Each must import in tooled's environment, on the same zeared tooled runs on. Optional when `[app]` or `trees` gives the policy commands. |
| `trees` | For a policy with no `[app]`: packages (or modules) whose parsed commands tooled reads, each a tree at the root, on the same parsed tooled runs on. |

Each zeared `Message` class becomes a **contract**, one of the operations `tooled operations` lists, sorted by what
it is, which decides its op:

| Kind | What makes it so | Op |
|---|---|---|
| retained | `RETAINED = True`, presence feed or not | `read` |
| stream | none of the others | `read` |
| presence feed | `LIVELINESS` alone | `read` |
| queryable | a `REQUEST`, or a declared `EFFECT` | `query` when `EFFECT = READ`; `invoke` otherwise, no effect declared included |

A contract is named by its class's name, and always answers to `module:Class` too. When two packages define classes
by the same name, the bare name is ambiguous: wherever it's used, in a rule or on the command line, it is refused,
naming both, and `module:Class` picks one.

## `[bind]`

Slot values every key renders with: a contract's topics, and a command's keys. A bound slot is fixed: a call that
gives it another value is refused, and `describe` and `operations` show it filled. Each value is one concrete chunk
of a key: no `/`, and no wildcard.

Binding is a guardrail and a convenience. Where a server checks a token against a slot, binding that slot saves
giving it each time; a policy that wants other values of a slot (another player's public state, say) leaves it
unbound. `tooled check` lists, under `fixed`, each open slot that the allow rules let only one value fill: a slot
every call must give that value, which is better bound.

## `[inject]`

Request fields every request that declares them carries, each from a source:

| Source | Keys |
|---|---|
| An environment variable | `env = "NAME"` |
| A file's whole content, stripped | `file = "path"` |
| A value in a TOML file | `toml = "path"`, `key = "…"`; and, to pick one table of an array of tables, `table = "…"` with `match = { … }`, which must match exactly one |
| A value made fresh for each call | `generate = "uuid"`: a uuid4, as 32 lowercase hex digits |

- A relative path is read from the policy file's own directory.
- **A path may hold variables:** `${NAME}`, or `${NAME:-default}`, the default when `NAME` is unset or empty, as in
  a shell. They're expanded as the source is read, then a leading `~`, so a default may start with one. An unset
  `${NAME}` with no default is refused, never read as empty. Only `file` and `toml` paths take variables, and only
  in that form: a bare `$NAME` is refused at load, with how to write it.
- An injected field is taken out of the schema `describe` shows, and a request that gives a value for it is refused.
- A value read from a source is never printed: not in a result, an error, or `check`'s summary, which shows only
  its source and length, and the path it read.
- There is no literal source, so a secret can't end up in the policy file.
- **A generated value** is an id a server can tell a repeat by, such as an order's. It is no secret: a reply may
  echo it. `check` names its source and makes none. A write that carries one is asked once more, with the same
  value, when nothing answers (see [cli.md](cli.md)).

```toml
[inject.order_id]               # every request that declares `order_id` carries a fresh one
generate = "uuid"

[inject.token]                  # where a client keeps it: under $XDG_CONFIG_HOME when that's set
toml = "${XDG_CONFIG_HOME:-~/.config}/demo/identity.toml"
table = "players"
match = { world = "w1", player = "ann" }
key = "token"
```

## Rules: `[[allow]]` and `[[deny]]`

| Key | Default | Meaning |
|---|---|---|
| `ops` | required | what's done. On the wire: `read`, `query`, `invoke`, `publish`, `unretain`; this version acts on `read`, `query` and `invoke`, and `publish` and `unretain` load, so a policy written now carries forward. On the tree: `see`, `do`, `confirm` (*On the tree*, below). A rule's ops are one route's |
| `operations` | `["*"]` | what it's done to: globs over the names `tooled operations` lists, or `module:Class`; a mounted command's are led by its prefix (`relay/*`). It was `contracts` before 0.2.4; that key is refused, naming this one |
| `keys` | `["**"]` | Zenoh key expressions (`*` one chunk, `**` any number, `$*` within a chunk), over a contract's topics or a command's keys |
| `variants` | none | the wire's only: globs over the tags of a request's root union; none covers the whole contract |
| `fields` | none | a deny rule's only, on the wire: globs over the optional fields at a request's root, which it hides; none covers the whole call |

**Nothing is allowed until a rule allows it, and deny wins.** A rule covers a contract when its `ops` name the
contract's op and one of its `operations` globs matches the contract's name. Then its keys decide, with Zenoh's own
matching:

- **What's shown.** A contract is shown when an allow rule's keys intersect one of its topics (as `[bind]` renders
  it) and no deny rule covers all of them. `operations`, `describe` and every call see only what's shown.
- **What a fetch, a query or a write may reach.** A call renders one selector: the topic with its given and bound
  slots filled, the rest widened. It is allowed when an allow rule's keys include the whole selector, and no deny
  rule's keys intersect it. Replies carry no keys to filter by afterwards, so a selector that could reach a denied
  key is refused outright, with a hint to narrow the slots. A selector no allow rule covers is refused naming each
  open slot an allow rule's keys fix, with its value when they fix it to one (`give world=w1`), else `…`. A write
  (`invoke`) is never widened: every slot must be given or bound, so it reaches one key.
- **What a stream hands on.** `listen` and `wait` check each sample's key, which it does carry: a sample on a
  denied key is left out and counted, rather than the whole stream refused.

**Variants.** A rule with `variants` covers only the request-union tags its globs match. A deny rule with variants
refuses those tags, not the contract, and `describe` leaves them out of the request's schema. An allow rule with
variants allows only those tags.

The tag is read from the request as given, before it loads, so a denied variant is refused (exit 3) whatever its
fields hold, and no error describes them. An error about a tag the union doesn't have names only the tags the
policy shows.

**Only a union at the request's root is checked.** A union nested deeper, such as one inside a list of follow-on
steps, carries tags no rule sees. Where those tags matter, the server's own checks are what stop them, or the
policy hides the field that holds them, or denies the contract.

**Fields.** A deny rule with `fields` denies no call, only the request fields its globs match: the optional fields
at the request's root, its own and, for a call that carries a variant, a flat union variant's. Such a field is
hidden. A call that gives it is refused (exit 3), read from the request as given before it loads, as a tag is;
`describe` and `tooled tools` leave it out, so a profile can keep a field out of a model's reach (a chain of
follow-on orders, say) without denying the operation:

```toml
[[deny]]                        # orders, one at a time
ops = ["invoke"]
operations = ["OrderResult"]
fields = ["then"]
```

A required field can't be left out of a call, so it can't be hidden: a glob passes over it, and a name without a
glob that names one is refused at load. With `variants` too, the rule covers only those variants' calls. Like any
rule, it covers what its `ops`, `operations` and `keys` reach; the tools and `describe` leave a field out only
where its keys cover every topic, and a call at a key they reach is refused either way. `fields` on an allow rule
is refused: a field is hidden, never allowed one by one.

### On the tree

A command (*`[app]`*, below) takes one of the tree's ops, as a contract takes one of the wire's:

| Op | Covers |
|---|---|
| `see` | running a command that declares `EFFECT = READ` |
| `do` | running any other command: `WRITE`, `ACTION`, or no `EFFECT` at all, so the default fails safe |
| `confirm` | answering yes when a command asks before it acts; the call needs its `see` or `do` too, at the same key |

**`see` runs a read.** It isn't about what a listing shows: `operations` and `describe` show what any rule allows,
whatever its ops, as on the wire.

- **Keys are a command's keys:** its phrases' (`fleet/{fleet}/mine`), each led by its mount's prefix (`relay/run`).
  A command is shown when an allow rule's keys intersect one of them, and a call is allowed when an allow rule's
  keys include the key the call's line comes to (`fleet/f1/mine`) and no deny rule's intersect it. A phrase's slot
  of several words, or the rest of the line, is no part of a key, and nor is a flag: a rule over `fleet/*/sell`
  holds whatever is sold.
- **`[bind]` narrows a command's slot** by its name, as a topic's: `fleet = "f1"` makes `fleet/{fleet}/mine` read
  `fleet/f1/mine`, and a line naming another fleet is refused.
- **A key holds no separator or wildcard.** A slot's value holding `/`, `*`, `$`, `?` or `#` would make the key a
  pattern, or longer than its phrase, so the call is refused, naming the slot, and the kind whose resolver should
  map the value to a key (`map it to a key in the resolver of 'station'`).
- **One route a rule:** a rule's ops are the wire's or the tree's, not both, so its keys are read in one space. A
  name written without a glob must be one its ops reach (a contract for the wire's, a command for the tree's), and
  `variants` and `fields`, which are about a contract's request, are refused on a tree rule.

```toml
[[allow]]                       # every read, and the relay's commands
ops = ["see"]

[[allow]]
ops = ["do", "confirm"]
operations = ["relay/*"]

[[deny]]                        # never the gateway's
ops = ["see", "do", "confirm"]
keys = ["gateway/**"]
```

**Names must name something.** A contract or command name, a variant tag, a hidden field, a bound slot or an
injected field written without a glob must exist in the protocol or the app, so a typo is refused at load. A glob
may match nothing.

## `[app]`

An app's command trees: parsed's commands, beside the protocol's contracts.

| Key | Meaning |
|---|---|
| `factory` | `pkg.module:function`: called with nothing, it returns the app (parsed's `App`) whose mounts hold the commands. It's called to read them, so it should be cheap: definitions that import without the app's runtime, handlers by their paths. |

```toml
[app]
factory = "demo.client.app:make"
```

Each mount's commands become operations, led by its prefix: a group mounted at the root names its commands by class
(`FleetMine`, `demo.client.fleet:FleetMine`), and one mounted under `relay` by its words, slashed (`relay/Run`,
`relay/demo.service:Run`). So a package mounted twice, under two prefixes, gives each of its commands twice, one a
mount. A command's keys are its phrases' (parsed's `CommandDoc.keys`), led the same way: `fleet/{fleet}/mine`,
`relay/run`. Its effect is its `EFFECT`, read on zeared's scale (`read`, `write`, `action`), and none when it
declares none.

A policy gives commands by `[app]` or by `[protocol] trees`, not both, and it needs one of those or a protocol
package. `check` names the app or the trees, and counts the commands it read.

## Not in this version

`[[keepalive]]` and `[protocol] extension` are refused with the reason: they arrive with `tooled serve` and with
extensions. So is `[app] state`, which builds what commands run with: it arrives with running them.
