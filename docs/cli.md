# The command

```
parsed VERB [CONTRACT] [slot=value …] [--policy PATH] [options]
```

Every verb takes `--policy` after the verb (else `$PARSED_POLICY`, else `./parsed.toml`; see
[policy.md](policy.md)). `parsed --version` names parsed and the seared and zeared it runs on.

## Verbs

| Verb | Acts on | Prints |
|---|---|---|
| `check [--connect] [--no-resolve]` | the policy | what it shows, by operation; each injected field's source, and the length of one read (a generated one is never made); with `--connect`, whether the session opens; with `--no-resolve`, the same without reading any inject source |
| `contracts` | the policy | one line of each contract it shows: kind, verbs, topic (bound slots filled), slots left to give, summary |
| `describe CONTRACT [--reply \| --variant TAG]` | any shown contract | the contract, layered (below) |
| `fetch CONTRACT [slot=value …]` | a retained contract | its values, sorted |
| `query CONTRACT [slot=value …] [--request JSON]` | a queryable that declares `EFFECT = READ` | its replies, in the order they arrived |
| `listen CONTRACT [slot=value …]` | a retained contract or a stream | one JSON line per value as it arrives |
| `invoke CONTRACT [slot=value …] [--request JSON] [--dry-run]` | a queryable that writes: `EFFECT = WRITE` or `ACTION`, or none declared | its replies, in the order they arrived; with `--dry-run`, what it would send |

A contract is its class's name, or `module:Class`. Presence feeds have no verb in this version; `contracts` lists
them with none, and a verb that doesn't act on a contract says which ones do.

### Slots

`slot=value` narrows a read to keys whose slot has that value; a slot left out widens to every value, and a bound
one is filled by the policy (another value for it is refused). A value is one chunk of a key, or several for a
trailing `{name**}` slot, and may hold Zenoh's `$*` to match part of a chunk (`station=s$*`). A write is the
exception: it reaches one key, so it takes every slot (below).

### `describe`

`describe` is layered, so even a large contract reads in a few kilobytes:

- `slots`: each slot's schema, or `{"bound": value}`;
- `request`: the request's JSON Schema: seared's load schema, closed against unknown keys, without the fields the
  policy injects or the variants it denies. A root union shows folded, as its list of tags, with `variants` beside
  it (each tag and a line of what it does);
- `reply`: a field list, each top-level field's name, type and doc;
- `more`: the commands that go deeper: `--reply` for the reply's whole dump schema, `--variant TAG` for one
  variant's fields.

### `query`

`--request` is the request in the protocol's own wire form: JSON, `-` to read stdin, or `@path` to read a file.
Leave out the fields the policy injects; a value given for one is refused. The request is loaded strict by its own
class before anything is sent, so a missing field, a wrong type or an unknown key is refused with exit 4, and
`path` names the field. `--param k=v` adds a selector parameter (a chronicler's history read takes `from`, `to`,
`limit`, `order`). `--timeout S` bounds the wait, 5 seconds by default; no reply is a note, not an error.

### `invoke`

`--request` is given as `query`'s is, and checked the same way before anything is sent: the fields the policy
injects (a secret, a generated id) are added, a value given for one is refused, and the request is loaded strict
by its own class. A write reaches one key: every slot must be given or bound, and none may hold a wildcard, or the
call is refused with exit 2. The replies print as a JSON array; a refusal the server sends back (`"ok": false`,
say) is a reply, and exits 0. So is an error reply: it's noted, and the result is empty. `--timeout S` bounds the
wait, 5 seconds by default.

**A lost reply.** When nothing answers within the timeout, a write is asked once more, with the same request, if
it carries a generated id (`[inject.order_id] generate = "uuid"`, say): a server that tells a repeat by its id
answers with the result it gave before, rather than doing the work twice. A note says so. The retry waits the
timeout again; `--no-retry` turns it off. A request with no generated id is never repeated, since nothing would
tell the repeat from a second write. Still nothing, and it exits 5, with the generated values to look for the
effect by:

```json
{"error": "mesh", "message": "no reply to MoveResult within 5s, after one retry: it may or may not have been carried out",
 "generated": {"move_id": "3f9a0c27b1e44d6c8e1a2b3c4d5e6f70"}}
```

`--dry-run` does all of that but send, and prints what it would have sent:

```json
{"selector": "demo/w1/move/ann", "variant": null, "request": {"token": "<injected>", "to": "dock"}}
```

Each injected secret shows as `"<injected>"`. A generated value shows the one this call made; a real call makes a
fresh one. `variant` is the tag of the request's root union, when it has one.

### `listen`

Values arrive one JSON line each, flushed as they come: a retained contract's current values first, then live ones.
`--meta` wraps each as `{"key", "origin", "at", "value"}`, where `origin` is `replay` or `live`. It stops after
`--count N`, after `--for DURATION` (`30`, `30s`, `5m`, `1h`), or on SIGINT or SIGTERM, always with exit 0.

## Output

- **Results** go to stdout as JSON, indented on a terminal or with `--pretty` (`listen`'s lines are always compact).
- **Errors** go to stderr as one JSON object: `{"error": kind, "message": …, "path": […]}`.
- **Notes** go to stderr as `{"note": …}` lines: an error reply, a reply that would not decode, no reply, a retry,
  samples left out of a stream, a dry run. A library's warning goes there as `{"log": level, "logger": …,
  "message": …}`.
- **Bytes** fields print as `{"bytes": n, "sha256": …}`, not their base64.

### Size

A result over the cap is refused rather than printed, with exit 6 and the size of each part, the biggest first:

```json
{"error": "too_large", "message": "the result is 261,443 bytes, over the 28,000-byte cap: …",
 "size": 261443, "cap": 28000, "parts": {"items": 1, "bytes": {"hulls": 192346, "parts": 40100, "sectors": 19646}}}
```

The cap is `--max-bytes N`, else `$PARSED_MAX_BYTES`, else 28,000 bytes; `0` removes it. Then:

- **`--select PATHS`** keeps parts of a result, structure kept: comma-separated paths, dotted (`size.w`), with `[]`
  mapping over a list (`systems[].key`). It applies to each item of a list result (`fetch`, `query`, `invoke`,
  `contracts`), and to each line of `listen`. A path that matches nothing in a non-empty result is refused, so a
  typo shows.
- **`--out FILE`** writes the whole result to a file, uncapped, and prints `{"written": path, "bytes": n}`. Every
  verb but `listen` takes it.

In `listen`, a line over the cap is left out and noted at the end.

## Exit codes

| Code | Kind | Meaning |
|---|---|---|
| 0 | | done, an empty result included |
| 2 | `usage`, `policy`, `catalog` | the command, its policy file or its protocol can't be used as given |
| 3 | `refused` | the policy refuses it |
| 4 | `validation` | the request doesn't load as its class; `path` names the field |
| 5 | `mesh` | the session wouldn't open, a read or a write failed, or a write got no reply (`generated` holds its generated values) |
| 6 | `too_large` | the result is over the size cap |
