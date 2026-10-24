# Tools for a harness

`tooled tools` prints the operations a policy shows as tool definitions: what a harness hands a model, so the model
can ask for a call by name with typed arguments. It's for a model that calls tools rather than a shell, a local one
on llama.cpp or Ollama say. An agent with a shell, Claude Code among them, uses the verbs themselves
([agents.md](agents.md)).

```sh
tooled tools --format openai --policy agents/player.toml --out tools.json
tooled tools --sizes --policy agents/player.toml
tooled tools --verbs fetch,query,invoke,wait:Fleet* --policy agents/player.toml --out tools.json
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

A request with a union at its root becomes **one tool per variant** the policy allows, its tag fixed by the tool:
`query_look_reply_ping` and `query_look_reply_pong`, but no tool for a `dev-peek` the policy denies. A retained
contract has no listen, which would give what its fetch does. A presence feed has no verb yet, so no tool; a note on
stderr names the ones the policy shows.

Each tool is a call of the verb its name begins with: its slots are that verb's `slot=value` arguments, and the
rest its `--request` (with the variant's tag added), its `--until` conditions, or its `--count`; `timeout` is a
wait's `--timeout` and a listen's `--for`, 30 seconds by default.

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
  what the policy injects (`token`) and without the union's tag (`kind`). A flat union's variant sits beside the
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
at once:

```json
{"error": "policy", "message": "2 fields can't be said in a tool's schema; deny them in the policy: DryRunReply 'tries[]' (a union), OrderResult 'then[]' (a union)"}
```

Deny the operation, or the variants, in the profile the tools are for. tooled still checks every call against the
policy and loads its request with seared; the schema only helps the model write a call that will pass.

**Other refusals:** a slot that shares its name with another parameter, a request field or a wait's `until`, say
(bind the slot, and the other is the one parameter); a request with two unions at its root; a name over 64
characters, or two tools of one name.

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
