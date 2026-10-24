# Tools for a harness

`tooled tools` prints the operations a policy shows as tool definitions: what a harness hands a model, so the model
can ask for a call by name with typed arguments; `tooled call` carries out each call it asks for. It's for a model
that calls tools rather than a shell, a local one on llama.cpp or Ollama say. An agent with a shell, Claude Code
among them, uses the verbs themselves ([agents.md](agents.md)).

```sh
tooled tools --format openai --policy agents/player.toml --out tools.json
tooled tools --sizes --policy agents/player.toml
tooled tools --verbs fetch,query,invoke,wait:Fleet* --policy agents/player.toml --out tools.json
tooled call wait_fleet_state --args '{"fleet": "ann.f1", "until": {"docked": true}}' --policy agents/player.toml
```

The examples here read the small protocol [policy.md](policy.md) describes, under a policy that binds `world`,
injects `token`, and allows reads, queries and `MoveResult`'s write.

## Which operations are tools

Each tool is one call of one verb:

| Contract | Tool | Its parameters |
|---|---|---|
| retained | `fetch_…` | its slots, each optional: left out, every value |
| a queryable that declares `EFFECT = READ` | `query_…` | its slots, each optional; its request |
| any other queryable | `invoke_…` | its slots, each required (a write reaches one key); its request |
| retained, or a stream | `wait_…` | its slots, each optional; `until`, its conditions; `timeout` |
| a stream | `listen_…` | its slots, each optional; `count`, 1 by default; `timeout` |
| a command that declares `EFFECT = READ` | `see_…` | its arguments, parsed's (*On the tree*, below) |
| any other command | `do_…` | its arguments; `confirm`, for one that asks first |
| a chain | `do_…`, once | its shared arguments, and `steps` |

A request with a union at its root becomes **one tool per variant** the policy allows, its tag fixed by the tool:
`query_look_reply_ping` and `query_look_reply_pong`, but no tool for a `dev-peek` the policy denies. A retained
contract has no listen, which would give what its fetch does. A presence feed has no verb yet, so no tool; a note on
stderr names the ones the policy shows.

Each tool is a call of the verb its name begins with, and `tooled call` carries it out as that verb: its slots are
the verb's `slot=value` arguments, and the rest its `--request` (with the variant's tag added), its `--until`
conditions, or its `--count`; `timeout` is a wait's `--timeout` and a listen's `--for`, 30 seconds by default.

### Waiting

A wait's `until` has one property for each top-level field of the value that holds a single value: a string, a
number, a boolean, or one of an enum's members, typed as the field is. A field that holds more (a list, a map, a
nested object) has none, and nor does a field a bound slot fixes. The model gives at least one, and the call waits
for a value whose fields equal every one given:

```json
"until": {
  "type": "object",
  "properties": {
    "station": {"type": "string", "description": "the station's key"},
    "stock": {"type": "integer", "minimum": 0, "description": "units in stock"},
    "closed": {"type": "string", "maxLength": 80, "description": "why it is closed, when it is"}
  },
  "minProperties": 1,
  "additionalProperties": false
}
```

`tooled wait`'s dotted paths into nested fields aren't a tool's: a wait on one needs the command.

### On the tree

A command the policy shows is a tool of its op's verb, `see` or `do`, named for it with its mount's prefix
(`see_relay_status`, `do_station_dock`). Its parameters are its arguments as parsed writes their schema
(`parsed.doc.json_schema`), already closed, in the dialect:

- **the slots the policy binds** left out;
- **each name's kind** said in its field's description (`the station's key (a station)`), and the other `x-` keys
  left out;
- **`confirm`** for a command that asks before it acts: `true` answers its question yes where the policy allows
  `confirm`; left out, the question comes back unanswered and nothing is done.

**A chain is one tool,** its steps never their own: its shared arguments, and `steps`, an array of the steps the
policy shows in order, each a closed object tagged by `step` (`{"step": "jump", "system": "sol"}`), a union a
constrained decoder holds. **An interactive command is never a tool,** whatever the policy says; a note on stderr
names the ones it shows, and each one's `INSTEAD` is a tool when the policy shows it.

`tooled call` loads a command's arguments into the command (a chain's into its steps), adds the bound slots, says it
as a line (`app.render`, in the mount that holds it) and runs it as `tooled see` or `tooled do` runs a line: judged
by the policy first, then parsed's envelope printed, with its exit code.

### Picking tools

The policy says what may be done; `--verbs` says which of it a harness offers. Each entry is a verb, for all of
its tools, or `verb:GLOB`, for those whose operation the glob names, by class name or `module:Class`:

```sh
tooled tools --verbs fetch,query,invoke,wait:FleetState,wait:ShipState
```

Waits cost the most, since `until` types every field: a profile that fetches twenty contracts may only ever wait on
one or two. A pattern with no glob in it must name an operation, so a typo is refused, not silently empty.

## A tool

```json
{
  "type": "function",
  "function": {
    "name": "query_look_reply_ping",
    "description": "Ask a station how it is.",
    "parameters": {
      "type": "object",
      "properties": {
        "station": {"type": "string", "description": "the station's key"},
        "player": {"type": "string", "description": "the player's key"},
        "ask_id": {"type": "string", "description": "an id for this look, to tell a repeat by"}
      },
      "required": ["station"],
      "additionalProperties": false
    }
  }
}
```

- **Parameters:** the unbound slots, then the request's fields as the protocol writes them on the wire, without
  what the policy injects (`token`) or hides ([policy.md](policy.md), *Fields*) and without the union's tag
  (`kind`). A flat union's variant sits beside the
  request's own fields; a nested one's sits under its payload key, as on the wire.
- **The description** is a docstring's first line: a variant's, for a variant's tool; a write's request's; else
  the contract's, which says what a read gives. A wait's and a listen's then say what they do.
- **The name** is the verb, the class's name in snake case, and the variant's tag, each run of other characters
  one `_`: `invoke_move_result`, `query_look_reply_dev_peek`. A class whose bare name two packages share is named
  by `module:Class`: `fetch_demo_protocol_station`. Every name fits `[A-Za-z0-9_-]{1,64}`.

## The schemas

Every parameter schema is in a dialect a constrained decoder holds, llama.cpp's grammar converter among them, so
the model can only write arguments of the right shape:

- references inlined, and no `$schema`, `title` or `$defs`;
- every object with `properties` closed by `additionalProperties: false`;
- required properties first;
- seared's constraints kept (`minimum`, `maxLength`, `pattern`), and each field's doc as its `description`.

**A union can't be said in it,** except at a request's root, where it becomes one tool per variant. A field that
holds one anywhere else (a list of chained orders, say) is refused, and the export with it, naming every such field
at once, and what leaves it out:

```json
{"error": "policy", "message": "2 fields can't be said in a tool's schema; hide or deny them in the policy: DryRunReply 'tries[]' (a union; hide 'tries'), OrderResult 'then[]' (a union; hide 'then')"}
```

An optional field at the request's root can be hidden, in the profile the tools are for, and the operation keeps
its tools without it:

```toml
[[deny]]                        # a small model's orders: one at a time, nothing tried first
ops = ["query", "invoke"]
fields = ["then", "tries"]
```

Anything else, a required field or one inside another, takes denying the operation, or the variants. tooled still
checks every call against the policy and loads its request with seared; the schema only helps the model write a
call that will pass.

**Other refusals:** a slot that shares its name with another parameter, a request field or a wait's `until`, say
(bind the slot, and the other is the one parameter); a request with two unions at its root; a name over 64
characters, or two tools of one name.

## Calling a tool

`tooled call TOOL --args JSON` carries out one tool call as the model wrote it, and prints what the harness hands
back. `--args` is the call's arguments, a JSON object (`-` for stdin, `@path` for a file); left out, `{}`.

```sh
tooled call query_look_reply_ping --args '{"player": "ann", "station": "s1"}' --policy agents/player.toml
```

- **What the tool says is checked first:** every required parameter given; none it doesn't take; a slot one value
  of its type; a condition on a field `until` lists, of that field's type (one of its values, when it lists them);
  `count` and `timeout` in range. A refusal is a `validation` error (exit 4), worded for the model, with the
  parameter in `path`:

  ```json
  {"error": "validation", "message": "wait_station can't wait on 'stok'; 'until' takes 'station', 'stock' and 'closed'", "path": ["until", "stok"]}
  ```

- **Then it's the verb's call.** The policy and seared check it as they check the verb: a request's fields are
  seared's to judge, and a field the policy injects or hides is refused (exit 3) with the policy's reason. It prints
  what the verb prints, with the verb's exit code: a fetch's values, a query's or a write's replies, the value a
  wait found, or exit 7 with the last one seen.
- **A command's call** is run as its line: the envelope `see` and `do` print, with their exit codes; a bound slot
  given another value is refused, and what the command refuses (a step the chain hasn't, a value of the wrong type)
  is a `validation` error naming it, as `steps[1].system`.
- **A listen's values come as one JSON list,** once `count` have come or `timeout` is up; a note says when fewer
  came. A fetch, a query and a write wait 5 seconds on the mesh, as the verbs do by default.
- **A ledger:** with `TOOLED_LOG` set, each call appends a line naming its tool, operation and variant, the names
  of its arguments, its exit code and error kind, and its bytes and seconds ([cli.md](cli.md), *The call log*).
- **Any tool the policy shows,** not only those `--verbs` kept: picking shapes what a harness offers, and the
  policy is the guard. A name the policy shows no tool of is a usage error (exit 2) naming the closest. Only the
  tool called need be one the dialect can say, so a profile that exports with picks calls the same way.

## Formats

`--format` picks the form; each prints a JSON array of tools, sorted by name:

| `--format` | Each tool | For |
|---|---|---|
| `openai` (the default) | `{"type": "function", "function": {"name", "description", "parameters"}}` | chat completions: llama.cpp, Ollama, vLLM |
| `anthropic` | `{"name", "description", "input_schema"}` | Messages |
| `mcp` | `{"name", "description", "inputSchema"}` | an MCP server's `tools/list` |

**Byte-stable:** the same policy and protocol print the same bytes, so a model server's prompt cache, which keys on
the tools at the head of every request, keeps hitting. A profile that shows fewer operations is a shorter, cheaper
prefix.

## Sizes

`--sizes` prints each tool's size instead, in bytes as `tools` prints it and in tokens estimated at four bytes
each, with the totals:

```json
{"format": "openai", "tools": [{"name": "fetch_station", "bytes": 254, "tokens": 64}, …],
 "total": {"tools": 9, "bytes": 4851, "tokens": 1216}}
```

A whole profile's tools can pass the 28,000-byte cap; `--out FILE` writes them whole.
