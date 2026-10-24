# The command

```
tooled VERB [OPERATION] [slot=value …] [--policy PATH] [options]
```

Every verb takes `--policy` after the verb (else `$TOOLED_POLICY`, else `./tooled.toml`; see
[policy.md](policy.md)). `tooled --version` names tooled and the seared, zeared and parsed it runs on. A call given
`--session` (else `$TOOLED_SESSION`) runs in a session that holds the policy open ([Sessions](#sessions)).

## Verbs

| Verb | Acts on | Prints |
|---|---|---|
| `check [--connect] [--no-resolve]` | the policy | how many operations its protocol and its app's commands come to (with an app or trees, which, and how many commands, and under `unmarked` the shown commands that declare neither `EFFECT` nor `INTERACTIVE`; where commands and contracts overlap, each overlap and the side shown, and the denies that don't carry over), what it shows by op, and how many it hides; under `fixed`, each shown operation's open slots that the allow rules let only one value fill, with that value (bind them, or every call must give it); each injected field's source, the length of one read, and the path it read, variables expanded (a generated one is never made); with `--connect`, whether the mesh's session opens, and the app's on the state it builds (`session`); with `--no-resolve`, the same without reading any inject source or expanding any variable |
| `operations` | the policy | one line of each operation it shows, contracts and commands: route, then a contract's kind, verbs, effect and topic (bound slots filled), or a command's verbs, effect and phrase; the slots left to give, the summary, and an interactive command's `instead` |
| `describe OPERATION [--reply \| --variant TAG]` | any shown operation | the operation, layered (below) |
| `fetch CONTRACT [slot=value …]` | a retained contract | its values, sorted |
| `query CONTRACT [slot=value …] [--request JSON]` | a queryable that declares `EFFECT = READ` | its replies, in the order they arrived |
| `listen CONTRACT [slot=value …]` | a retained contract or a stream | one JSON line per value as it arrives |
| `invoke CONTRACT [slot=value …] [--request JSON] [--dry-run]` | a queryable that writes: `EFFECT = WRITE` or `ACTION`, or none declared | its replies, in the order they arrived; with `--dry-run`, what it would send |
| `wait CONTRACT [slot=value …] --until PATH=VALUE [--until …]` | a retained contract or a stream | the first value that meets every condition |
| `see [options] <words…>` | a command that declares `EFFECT = READ` | parsed's envelope for the line (below) |
| `do [options] <words…>` | any other command | parsed's envelope for the line, a question it asks first among it |
| `call TOOL [--args JSON]` | any tool the policy shows | what the verb its name begins with prints, a listen's values as one JSON list, a command's envelope ([tools.md](tools.md), *Calling a tool*) |
| `session start \| run \| stop \| status` | a session | where a session is, how it ended, or how it is ([Sessions](#sessions)) |
| `serve [--session NAME] [--verbs VERBS]` | a session | MCP's messages on stdout, answering a harness's on stdin: the tools a policy shows, carried out in the session ([tools.md](tools.md), *Serving over MCP*) |
| `tools [--format openai\|anthropic\|mcp] [--verbs VERBS] [--sizes]` | the policy | the operations it shows as tool definitions for a harness: one for each fetch, query, write, wait or listen, and one for each allowed variant of a request's union; one for each command (`see_…`, `do_…`) and chain, but an interactive one ([tools.md](tools.md)); `--verbs fetch,wait:Fleet*` keeps some verbs' tools, or some operations'; with `--sizes`, each one's bytes and estimated tokens |

An operation is anything tooled lists and acts on; each is a contract in this version. An operation, a contract
included, is its class's name, or `module:Class`. Presence feeds have no verb in this version; `operations` lists
them with none, and a verb that doesn't act on a contract says which ones do. `tooled contracts`, the listing's name
before 0.2.4, is refused naming `operations`.

`--timeout S` bounds how long a verb waits on the mesh: 5 seconds for `fetch`, `query` and `invoke`, 30 for
`wait`. It bounds the whole of a `fetch`, a contract with several topics too. A topic whose queryable didn't answer
in time is noted (`a queryable did not answer in time`), and the values the others gave are kept.

### Slots

`slot=value` narrows a read to keys whose slot has that value; a slot left out widens to every value, and a bound
one is filled by the policy (another value for it is refused). A value is one chunk of a key, or several for a
trailing `{name**}` slot, and may hold Zenoh's `$*` to match part of a chunk (`station=s$*`). A write is the
exception: it reaches one key, so it takes every slot (below).

### `describe`

`describe` is layered, so even a large contract reads in a few kilobytes. A contract's document, beside its
`route` (`wire`), kind, op, verbs, effect and topics:

- `slots`: each slot's schema, or `{"bound": value}`;
- `request`: the request's JSON Schema: seared's load schema, closed against unknown keys, without the fields the
  policy injects or hides or the variants it denies. A root union shows folded, as its list of tags, with
  `variants` beside it (each tag and a line of what it does);
- `reply`: a field list, each top-level field's name, type and doc, and its `constraints` when it declares any:
  seared's, by the names a declaration gives them (`{"ge": 0}`, `{"max_length": 80}`, `{"pattern": "^[a-z]+$"}`),
  which on a list or a map hold for each value. The request's schema carries the same constraints as JSON Schema
  keywords (`minimum`, `maxLength`, `pattern`);
- `more`: the commands that go deeper: `--reply` for the reply's whole dump schema, `--variant TAG` for one
  variant's fields;
- `overlaps`: the commands that do what it does, when there are any.

A command's document, beside its `route` (`tree`), op (`see` or `do`) and effect:

- `phrases` and `keys`: how it's said, and its keys for policy, each led by its mount's prefix;
- `slots`: each slot's schema, or `{"bound": value}`; and `flags`, each as a line writes it, the field it sets, and
  whether it's a switch;
- `asks`, `interactive` and `instead`: whether it asks before it acts, whether it's only for a person at the client,
  and the command an agent says in its place;
- `overlaps`: the contracts it does the work of;
- `arguments`: its arguments' JSON Schema, parsed's: closed, with what only a command says as `x-` keys (`x-phrase`,
  `x-kind`, `x-asks`), the bound slots taken out;
- `reply`: its `REPLY` as a field list, or `null` for none; `--reply` gives its whole dump schema. `--variant` is a
  contract's.

A command's verb is its op's: `see` for a read, `do` for any other.

### `see` and `do`

The tree's verbs run a line of the policy's app (`[app]`, in [policy.md](policy.md)): `see` a read, `do` anything
else, each verb its op. tooled's own options stand first (`--policy`, `--select`, `--max-bytes`, `--out`,
`--pretty`, `--timeout`; `--` ends them); from the line's first word on, every word is the line's, a flag among
them. The words are joined as parsed's own command line joins them: a word with a space quoted, a single argument
the whole line.

```sh
tooled see fleets show frigate
tooled do fleet frigate jump vraelon then dock vraelon-station
tooled do move "Cla'allis Belt"           # one word: no shell quoting to get wrong past the shell's own
```

- **The line is judged before it runs,** against the app alone, so a refused line never opens its session. Each of
  its steps (one, or a chain's, joined by `then`) must be a command the verb runs (`see does not run FleetMine,
  which is no read; what does: do`), one the policy shows, at a key it allows and can hold.
- **Asking first:** a command that asks before it acts comes back with its question in `asks`, nothing done (exit
  0). `--yes` on the line answers it where the policy allows `confirm` at that key; elsewhere the line is refused,
  saying to leave `--yes` out, since parsed would answer it whatever tooled said.
- **Then it runs,** in the app's session on the state the policy builds, and prints parsed's envelope: `command`,
  `line`, `ok`, `done`, `asks`, `met`, `waited`, `reply`, `text` and `error`, under the size cap and `--select`
  (`--select reply.stock`). The exit code is the envelope's: 0; 2 for a line that doesn't read, with suggestions; 3
  refused (an `INTERACTIVE` command, with no person at a terminal, naming what to say instead); 4 a value refused;
  7 a wait past its bound; 1 a handler that failed.
- **Waits and watches** are the line's, on a read with a `REPLY`: `--until PATH=VALUE` (repeated for several) waits
  for a reply that meets every condition, and `--timeout 2m` bounds it (30s by default), exit 7 with the last reply
  past it; `--watch` writes the envelope as a line of JSON now and each time the reply changes, until `--count N`,
  the bound, its conditions, or SIGINT or SIGTERM (exit 0), each line after `--select` and under the cap, as
  `listen`'s are. The app's session reads the line again every second; it's state, not events.
- **Guarded against a person who isn't there:** an app may leave an editor or a viewer unmarked (`INTERACTIVE` is
  optional), so a line runs with an empty stdin, where a prompt reads nothing and fails at once, and a stdout of its
  own, where what a handler writes is kept out of the result (a note says how much). A line still running at
  `--timeout` (30 seconds by default; `30`, `30s`, `5m`) is given up on, exit 7 with `"last": null`. A line that
  waits on its own (`--until … --timeout 2m`) is given that bound and 5 seconds more, so parsed's own timeout, with
  its last reply, comes first; a `--watch` with no bound is given none. `tooled check` names, under `unmarked`, the
  commands a profile might rather deny.
- **A line that runs nothing,** one that doesn't read or asks for `--help`, isn't judged: parsed answers it. Its
  help and listings are the app's, and may name commands the policy doesn't show; running one is still refused.

### `query`

`--request` is the request in the protocol's own wire form: JSON, `-` to read stdin, or `@path` to read a file.
Leave out the fields the policy injects; a value given for one is refused. The policy comes first: the selector, and
the tag of the request's root union, are checked before the request loads (exit 3). Then the request is loaded
strict by its own class before anything is sent, so a missing field, a wrong type or an unknown key is refused with
exit 4, and `path` names the field. `--param k=v` adds a selector parameter (a chronicler's history read takes
`from`, `to`, `limit`, `order`). `--timeout S` bounds the wait, 5 seconds by default; no reply is a note, not an
error.

### `invoke`

`--request` is given as `query`'s is, and checked the same way before anything is sent: the fields the policy
injects (a secret, a generated id) are added, a value given for one is refused, and the request is loaded strict
by its own class. A write reaches one key: every slot must be given or bound, and none may hold a wildcard, or the
call is refused with exit 2. The replies print as a JSON array; a refusal the server sends back (`"ok": false`,
say) is a reply, and exits 0. So is an error reply: it's noted, and the result is empty. `--timeout S` bounds the
wait, 5 seconds by default.

`--select`'s paths are checked against the reply's schema before the write is sent (see Output), so a typo is
refused with nothing sent; a field the reply declares but a refusal leaves out is simply absent. Replies over the
size cap are refused with a message that says the write was sent and answered. A dry run checks `--select` against
the reply too, as the real call would, and prints its preview whole.

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

### `wait`

`wait` reads back what a write changed, in one call. It subscribes as `listen` does, and prints the first value
that meets every `--until`; a retained contract already in that state answers at once, from its replay.

- **Each `--until`** is a path and the value it must equal: `--until activity=idle`, `--until numbers.fuel=40`.
  The path is dotted, and a whole number picks a list's element (`ships.0.name`). The value reads as JSON if it
  can (`40`, `true`, `null`, `"30%"`), else as a string (`idle`, `-4,-1.0`); quote a string that would read as
  JSON (`--until 'name="true"'`). A number never equals a boolean, and `40` equals `40.0`.
- **Several `--until`s** must all hold for one value. They are separate flags, since a value may hold a comma.
- **`--timeout S`** bounds the wait, 30 seconds by default. Past it, or on SIGINT or SIGTERM, it exits 7 with the
  last value seen on a selected key, or `null` for none. The message counts the values seen, and names an
  `--until` path none of them had:

  ```json
  {"error": "timeout", "message": "no FleetState value met activity=idle within 30s: 4 seen", "last": {…}}
  ```

- **`--meta`** wraps the value as `listen`'s does; the conditions read the value inside.
- **`--until` and `--select` paths** are checked against the contract's schema before the wait begins, so a typo
  (`--until activty=idle`) is refused at once, not after the timeout. `--select` keeps parts of the value, and of
  `last`; a `last` over the size cap is left out, and the message says so.

## Sessions

A session holds the policy's world open in a process of its own: the policy as it was loaded, its catalog and
rules, each value it injects, the mesh, and the app's session on its state. A call that attaches to it loads and
opens none of that, so it costs about what starting `tooled` does: a `see` on a small app, or a `fetch` on a
loopback mesh, takes about 0.08 s attached, against 0.37 s cold. Many sessions run side by side, on one policy or
several, each with its own state, injected values and mesh.

```sh
tooled session start --policy seat.toml --name seat-a        # prints where it is, once it's open
TOOLED_SESSION=seat-a tooled see fleets show frigate         # runs in it
tooled session stop seat-a                                   # closes it, its state left
tooled session run --policy seat.toml -- claude -p "…"       # a session for one command, closed when it ends
```

| Verb | What |
|---|---|
| `session start [--name N] [--idle D] [--for PID] [--foreground] [--timeout S]` | Opens a session, and prints, once it's ready, where it is and what it holds: `session`, `socket`, `pid`, `policy`, `mesh`, `app`, `idle`, `for`, `log`, and the `seconds` it took to open. One that doesn't open exits as a cold call would (5 for a mesh or an app's state that won't open, 2 for a policy that won't load), saying why. A name already open is refused; with none, one is made up. `--foreground` holds it in this process instead, for a supervisor (a systemd user unit, a container, a harness that keeps its own processes): it prints the same, then serves until it ends |
| `session run [--name N] [--idle D] -- COMMAND …` | Opens a session, runs the command with `$TOOLED_SESSION` naming it, and closes the session when the command ends, exiting with the command's code. The session is tied to `session run`'s own process, so it ends with it however it ends |
| `session stop [NAME]` | Closes a session (`NAME`, else `$TOOLED_SESSION`), and prints how it ended |
| `session status [NAME]` | One session's status: `calls`, `in_flight`, `last_call` and `started` beside where it is; or, naming none, every session under the runtime directory, each one that has ended listed once, then cleared |

**Naming one.** A session is named by a name (letters, digits, `.`, `_` and `-`, up to 64) or by its socket's path
(holding `/`), so a harness can keep the socket where it wants. A name's socket is
`$XDG_RUNTIME_DIR/tooled/<name>.sock` (`$TMPDIR/tooled-<uid>/` without the variable), beside its status
(`<name>.json`) and its log (`<name>.log`); a path's are beside it. The directory must be the user's alone (mode
0700), and the socket and files are made so.

**Attaching.** Every verb but `check` and `session`'s own attaches when given `--session NAME` (a line's verb takes
it among tooled's own options, before the line), else `$TOOLED_SESSION`; an empty value names none.
Nothing else attaches a call: no session is found by its policy or its directory, so two agents never share one by
accident. `check` reads the file on disk, and never attaches.

- **The same output:** an attached call writes what the call would write cold, byte for byte, and exits as it would.
  A `listen` or a watch comes line by line, as it would. The first SIGINT or SIGTERM asks the session to stop the
  call (a `listen` or a watch ends as a signal ends it cold, and a `wait` exits 7 with the last value), and a second
  ends the client at once.
- **What's the call's:** its options, `--timeout`, the cap (`--max-bytes`, `$TOOLED_MAX_BYTES`), `--out` and `@path`
  (against its working directory), its stdin where it reads it (`--args -`), and `$TOOLED_LOG`.
- **What's the session's:** the policy, the mesh, the values it injects and the state, read once at its start, in the
  environment of what started it. A generated value (`generate = "uuid"`) is still made for each write. A source that
  can't be read keeps the session from starting (exit 2), where a cold call fails only once it needs the value. A
  call naming another policy (`--policy`, or `$TOOLED_POLICY` in its environment) is refused, exit 2. Changing a
  policy, or a token a file holds, means starting the session again.
- **A session named and not open** is refused, exit 5, saying how it ended when it knows (`no session 'seat-a' is
  open: it ended at …: idle for 1800s`). It's never run cold instead: whoever started the session believes its state
  is held.
- **Another tooled:** a call from a tooled of another version is refused, exit 2; start the session again with it.

**Over MCP:** `tooled serve --session NAME` is a client of a session too, so a harness's MCP tools and its agent's
command-line calls share one ([tools.md](tools.md), *Serving over MCP*).

**Inside a session,** calls run side by side, a `listen` or a watch beside the calls an agent makes, each with its own
streams; the wire's share the one mesh it holds, so a `listen` under a monitor costs the agent no session of its own. A
session holds its mesh for as long as it's open: zeared's `auto_reconnect` in `[mesh]` keeps it through a router's
restart, as it keeps any long-lived client. The app's session stays open between calls on a loop of its own, so a
state's own tasks (a heartbeat) go on running, and an app that makes its user present (a game's commander, say) keeps
them present while the session is open: that's the app's choice, in its `[app] state`. A line is guarded as it is cold:
an empty stdin, its handlers' output kept apart, and a line past its `--timeout` cancelled (exit 7), the session going
on. A handler that blocks the loop (a synchronous one, which can't be cancelled) holds every call on the session while
it blocks; past a line's bound, the session is wedged: each call on it fails (exit 5), and it ends without leaving its
state.

**How a session ends,** saying why in its status file:
- `session stop`, or SIGTERM, SIGINT or SIGHUP;
- the process it's for ends (`--for PID`, or `session run`'s own);
- it's idle: no call in flight and none for `--idle` (`30m`, `1h`; `0` for never). That's 30 minutes by default for
  a session tied to no process, and never for one tied to a process, since the process bounds it;
- it's wedged.

Its calls in flight are given 5 seconds to end, then stopped as their clients would stop them; the app's session is
closed, so its state is left, and then the mesh; and its socket is removed.

**Who can attach:** the same user, by the socket's mode and, where the system tells it, the peer's. A session holds
what the policy injects (a token, say), so a process that can reach its socket can act as the policy allows without
reading the token's file: a harness that sandboxes its agents lets the socket through, and nothing else of the
session's.

## Output

- **Results** go to stdout as JSON, indented on a terminal or with `--pretty` (`listen`'s lines are always compact).
- **Errors** go to stderr as one JSON object: `{"error": kind, "message": …, "path": […]}`. That includes a
  malformed argument, an unknown flag or a missing one, as `usage` naming the verb
  (`tooled fetch: argument --select: …`), never argparse's usage text. `--help` and `--version` print to stdout and
  exit 0.
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

The cap is `--max-bytes N`, else `$TOOLED_MAX_BYTES`, else 28,000 bytes; `0` removes it. Then:

- **`--select PATHS`** keeps parts of a result, structure kept: comma-separated paths, dotted (`size.w`), with `[]`
  mapping over a list (`systems[].key`). It applies to each item of a list result (`fetch`, `query`, `invoke`,
  `operations`), and to each line of `listen`.
  - **On a contract's values** (`fetch`, `query`, `invoke`, `listen`, `wait`), each path is checked against the
    contract's schema before the call. A path that names no field is refused (exit 2), before anything reaches the
    mesh, with the names nearest it: `--select 'stok' names no field of Station; did you mean 'stock'?`. A field
    the schema declares but a value leaves out (an optional one, say) is simply absent from that value. A map's
    keys and a `Bytes` field's `bytes` and `sha256` count as declared. A path into a shape the schema leaves open
    (an `Any` field) can't be judged this way, so it must match something, as on tooled's own documents.
  - **On tooled's own documents** (`check`, `operations`, `describe`), a path that matches nothing in a non-empty
    result is refused, so a typo shows.
- **`--out FILE`** writes the whole result to a file, uncapped, and prints `{"written": path, "bytes": n}`. Every
  verb but `listen` takes it.

In `listen`, a line over the cap is left out and noted at the end.

## The call log

With `TOOLED_LOG` set to a path, every run appends one JSON line to it as it ends, done or with an error, so a
harness keeps a ledger of what its agent ran without wrapping tooled:

```json
{"at": "2026-10-23T22:19:04.512Z", "verb": "call", "route": "wire", "session": null, "operation": "OrderResult",
 "tool": "invoke_order_result_jump", "variant": "jump", "arguments": ["fleet", "to"], "flags": ["--args", "--policy"],
 "exit": 0, "error": null, "path": null, "bytes": 61, "seconds": 0.84}
```

| Key | What |
|---|---|
| `at`, `seconds` | when the run began (UTC), and how long it took |
| `verb`, `route` | the verb (`null` for one tooled doesn't have), and `wire` or `tree` for a verb on an operation |
| `session` | the session it ran in, as the call named it; `null` for a call made cold |
| `operation`, `tool`, `variant` | what it acted on, as far as it got; a call's `tool` is the name it was given, found or not |
| `arguments`, `flags` | the names of what was given (a call's arguments, a verb's slots), and the options given |
| `exit`, `error`, `path` | the exit code, the error's kind, and where in the input it lies |
| `bytes` | what went to stdout: the result a harness hands back |

- **No values:** no slot's, request's, argument's or reply's, no error's message (which can quote one), and not a
  timed-out wait's `last`. Names, kinds and sizes are enough to compare one agent's runs with another's.
- **Every key on every line,** `null` where it doesn't apply.
- **The caller's log:** a call attached to a session appends its line to its own `$TOOLED_LOG`, not the session's,
  with what the session did and the call's own time.
- **One append a line,** so runs side by side share a file. A harness that keeps a record a turn gives each its own
  (`TOOLED_LOG=turns/<stamp>/calls.jsonl`).
- **What it can't see:** a run cut short (a crash, a kill, `--help`) leaves no line, and nor does what failed
  before tooled ran: a shell that couldn't parse the command, or a permission the harness refused. Those are in the
  harness's own record. A log tooled can't write is a note on stderr; the run's result and exit code stand.

## Exit codes

| Code | Kind | Meaning |
|---|---|---|
| 0 | | done, an empty result included |
| 2 | `usage`, `policy`, `catalog` | the command, its policy file or its protocol can't be used as given |
| 3 | `refused` | the policy refuses it |
| 4 | `validation` | the request doesn't load as its class, or a tool call's arguments don't fit its tool; `path` names the field |
| 5 | `mesh` | the session wouldn't open, a read or a write failed, or a write got no reply (`generated` holds its generated values); a session named isn't open, or is wedged |
| 6 | `too_large` | the result is over the size cap |
| 7 | `timeout` | `wait`: no value met the conditions in time; `last` holds the last one seen |
