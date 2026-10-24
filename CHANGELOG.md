# Changelog

Each version of tooled, newest first. tooled was named parsed until 0.2.3, and the entries before it say parsed, as
they were written.

A **Breaking** line means a command, a flag, an output or a policy file written for the version before may fail, or
behave differently.

The top entry, while it reads *unreleased*, collects what has merged for the next version; each change adds its own
lines as it lands, and the entry is dated when that version is released.

## 0.2.7 — unreleased

**Command trees, run.** The app's state, built from the policy.

### Added

- **`[app] state = "pkg.module:function"`:** called with the policy's `bound` and `injected` values as keyword
  arguments, it returns the state the app's session opens on, entered with the session and left after; handlers see
  it as `ctx.state`. An injected value needn't be a request's field when there's a state to take it. `check` names
  the state function, and `check --connect` opens the app's session on its state, beside the mesh, and closes it.

- **`tooled see` and `tooled do`:** a line of the app's, `see` for a read and `do` for any other, after tooled's own
  options, its words joined as parsed's command line joins them. It's judged before it runs (each step, a chain's
  too: its verb, what the policy shows, its key), then run in the app's session, and parsed's envelope printed
  under the cap and `--select`, with the envelope's exit code. A question comes back unanswered; `--yes` answers it
  where the policy allows `confirm`, and is refused elsewhere. A line that doesn't read, or asks for help, is
  parsed's to answer.

- **A guard for unmarked interactive commands:** a line runs with an empty stdin, so a handler that prompts fails at
  once, and a stdout of its own, its writes kept out of the result and noted. One still running at `--timeout` (30s
  by default) is given up on, exit 7; a line's own wait is given its bound and 5 seconds more.

### Changed

- **A command's `verbs`** in `operations` and `describe` are its op's, `see` or `do`, now that they run.
- **parsed `v0.1.8`,** one patch wide as before: an app whose commands tooled reads must pin the same.
- **`check --connect`** opens the app's session where there's an `[app]`, so a policy of commands alone may take
  it; only a policy with neither a mesh nor an app is a usage error.

## 0.2.6 — 2026-10-23

**Command trees, read.** parsed's commands join the catalog beside a protocol's contracts, from an app's mounts or
from packages read as trees, and the policy reaches them: the tree's ops `see`, `do` and `confirm` over command
keys, one route a rule, a profile showing one side where a command and a contract overlap, and no `[mesh]` needed
for commands alone. `operations` and `describe` show them; running them comes next.

### Added

- **Commands in the catalog:** with `[app] factory = "pkg.module:function"`, the function's parsed `App` gives its
  mounts' commands, each led by its mount's prefix: `relay/Run`, `relay/pkg.service:Run`, keyed `relay/run`. A
  package mounted twice gives each command twice, one a mount. `[protocol] trees` reads packages as trees at the
  root, for a policy with no app. A command's effect is its `EFFECT` on zeared's scale. `check` names the app or the
  trees, and counts the commands.
- **parsed as a dependency,** pinned at `v0.1.7`; `--version` names it beside seared and zeared, since an app whose
  commands tooled reads must pin the same parsed.

- **The policy over commands:** the tree's ops, `see` (a command declaring `EFFECT = READ`), `do` (any other, an
  undeclared one included) and `confirm` (answering yes to a command that asks first, beside its `see` or `do`).
  Their keys are a command's keys, led by its mount's prefix, and `[bind]` narrows a command's slot by its name. A
  call's key is refused when a slot's value holds `/`, `*`, `$`, `?` or `#`, naming the kind whose resolver should
  map it.
  `check` lists the commands a policy shows, by op, and their fixed slots.

- **Routes and overlaps:** a command overlaps a contract when it takes its fields from the contract's request class
  or replies with its class, and a profile shows one side, not both: `[protocol] route` (`tree` by default, or
  `wire`) picks where the rules would show both, and an allow rule naming one side without a glob takes it. The side
  not taken is refused naming the other. A deny that denies a contract wholly by name carries over to its commands;
  `check` lists the overlaps, and the denies by keys that can't carry.

- **Commands listed and described:** `operations` lists commands beside contracts, every row with its `route`
  (`wire` or `tree`) and `effect`, a command's with its phrase, and an interactive one's with the command to say
  instead. `describe` takes a command: its op, phrases and keys, slots, flags, asking first, the contracts it
  overlaps, its arguments' schema (parsed's, bound slots out) and its reply; a contract's document gains `route`,
  and the commands that overlap it. `check` names, under `unmarked`, the shown commands that declare neither
  `EFFECT` nor `INTERACTIVE`.

### Changed

- **`[mesh]` is optional** for a policy of commands alone, with no protocol packages; such a policy refuses a rule
  with a wire op, and `check` shows `"mesh": null` (`--connect` is a usage error). A policy with protocol packages
  still needs its mesh.
- **A rule is one route's:** its ops are the wire's or the tree's, so its keys are read in one space; one mixing
  them is refused, as are `variants` and `fields` on a tree rule, and a name without a glob its ops can't reach.
- **`[protocol] packages` is optional** where `[app]` or `[protocol] trees` gives the policy commands. A policy that
  names none of the three is refused, as is one with both trees and an app, and `[app] state` (it arrives with
  running commands).

## 0.2.5 — 2026-10-23

**Tools for harnesses.** The operations a policy shows, as tool definitions a harness hands a model that calls
tools rather than a shell: `tools` exports them, `call` carries out a model's call of one, and `TOOLED_LOG` keeps a
line a run. A deny rule's `fields` hides a request field from them, and refuses it in a call.

### Added

- **`tooled tools`:** one tool for each fetch, query, write, wait or listen the policy shows, and one for each
  variant of a request's root union it allows, in OpenAI's, Anthropic's or MCP's form (`--format`, `openai` by
  default). A tool's parameters are the unbound slots and the request's fields, without what the policy injects or
  the union's tag, in a schema dialect a constrained decoder holds: references inlined, objects closed, required
  first. A wait's `until` is typed from the value's top-level fields, so a model writes only conditions that can
  be met; a listen takes a `count`, and both a `timeout`. A field that holds a union anywhere but a request's root
  can't be said in the dialect, so the export is refused, naming every such field, for the policy to hide or deny.
  `--verbs` keeps some verbs' tools, or some operations' (`fetch,query,invoke,wait:Fleet*`), and `--sizes` gives
  each tool's bytes and estimated tokens. The output is sorted and byte-stable, so a model server's prompt cache
  keeps hitting.
- **`tooled call TOOL --args JSON`:** carries out one tool call as the model wrote it, for a harness to hand back
  what it prints. The arguments map back onto the verb the tool's name begins with (slots, the request with the
  variant's tag, a wait's conditions, a listen's count and timeout), and then it's that verb's call: checked by the
  policy and seared, printed as the verb prints, with its exit code. A listen's values come as one JSON list. What
  the tool says is checked first, each refusal a `validation` error (exit 4) naming the parameter, worded for the
  model: a required one missing, one it doesn't take, a slot or a condition of the wrong type, a value its field
  doesn't list. Any tool the policy shows can be called, not only those an export's `--verbs` kept.
- **The call log, `TOOLED_LOG`:** set to a path, every run appends one JSON line as it ends: when, the verb, the
  operation, tool and variant, the names of the arguments, slots and options given, the exit code, the error's kind
  and path, the bytes on stdout and the seconds taken. It keeps no value and no error message. Every key is on every
  line, and runs side by side share a file. A log tooled can't write is a note; the run's exit code stands.
- **Hidden request fields:** a deny rule's `fields`, globs over a request's optional top-level fields, its own or a
  flat union variant's. A hidden field is left out of the tools and `describe`, and a call that gives one is
  refused (exit 3) before the request loads; the rule denies nothing else. A required field can't be hidden: a
  glob passes over it, and a name without a glob that names one is refused at load, as is `fields` on an allow
  rule. With `variants`, only those variants' calls are covered.

### Changed

- **A refused field's `path`:** the policy's refusal of a request field it injects or hides, or of a bound slot
  given another value, names that field in the error's `path`, as a validation error does.

## 0.2.4 — 2026-10-22

**Operations, not contracts.** What tooled lists, names and looks up is an operation, so a later version can list a
command tree's commands beside a protocol's contracts; the listing and a rule's key take the new name. `describe`
shows a reply field's constraints, a refusal names the value an open slot needs when only one fits, and `check`
lists such slots for the policy to bind.

### Added

- **`describe` shows a reply field's constraints:** the field list gives each field that declares any its
  `constraints`, seared's, by the names a declaration gives them (`{"ge": 0}`, `{"max_length": 80}`), which on a
  list or a map hold for each value. `describe --reply` and a request's schema already carried them as JSON Schema
  keywords.
- **`check` lists the slots a policy would rather bind:** under `fixed`, each shown operation's open slots that the
  allow rules let only one value fill, with that value. Every call must give such a slot that value, so binding it
  in `[bind]` saves the agent the refusal.

### Changed

- **Breaking:** `tooled contracts` is `tooled operations`, which lists the same lines. `tooled contracts` is refused
  (exit 2), naming the new verb.
- **Breaking:** a rule's `contracts` key is `operations`: globs over the names `tooled operations` lists, or
  `module:Class`, as before. A rule that still says `contracts` is refused, naming the key to write instead. A
  rule's `ops` say what's done, and its `operations` what it's done to.
- **Breaking:** `check` counts `operations`, not `contracts`; `describe` takes an operation's name; and a name that
  names nothing is refused as `no operation named …`.
- **Breaking,** for a program that imports the package: a `Catalog` is made from operations, and `Catalog.of`
  reads classes, as the constructor did. `get` gives any operation, and `contract` a contract.
- **A refusal over an open slot names its value,** when every allow rule's key that reaches the call fixes the slot
  to one (`give world=w1`, where it said `give world=…`). Where more than one value fits, it still says `…`. The
  kind and exit code are unchanged (`refused`, 3).

## 0.2.3 — 2026-10-20

**parsed is now tooled.** The name parsed passes to a command-line library in the same family. The tool itself is
unchanged: its verbs, flags, policies, output and exit codes are as they were.

### Changed

- **Breaking:** the command is `tooled`, and `python -m tooled`; the package is `tooled`, and the repository
  `github.com/bwiswell/tooled`. Install it again under the new name: `uv tool uninstall parsed`, then
  `uv tool install "git+https://github.com/bwiswell/tooled@v0.2.3" --with <protocol-package>`.
- **Breaking:** the policy file is found at `--policy`, else `$TOOLED_POLICY`, else `./tooled.toml`, and the size
  cap reads `$TOOLED_MAX_BYTES`. The old names aren't read: a policy left at `./parsed.toml`, or named by
  `$PARSED_POLICY`, isn't found. Rename the file, or pass `--policy`.
- **Breaking:** the base of every error, for a program that imports the package, is `TooledError`, no longer
  `ParsedError`.

## 0.2.2 — 2026-10-20

**On seared and zeared 0.4.4, with errors that say what they are.** Every error is one JSON line, a malformed
argument's included; a refusal names the slot to fill; and a refused or invalid call is told as such whatever the
mesh's state.

### Changed

- **Dependencies:** seared `>=0.4.4,<0.5` and zeared `>=0.4.4,<0.5`, pinned to `tag = "v0.4.4"` each. **Breaking:**
  a protocol package parsed reads must pin the same tags, or uv refuses to install the two together. seared 0.4.4
  adds field constraints (bounds on `Int` and `Float`, lengths and a portable pattern on `Str`), which a request's
  or a reply's fields may carry: `describe` shows them in the schemas, and a request that fails one is refused
  before it's sent, as any invalid request is (exit 4). A `Union` tag that isn't a string is refused by seared too
  now, fallback or not. zeared 0.4.4's `fetch_retained` takes a timeout and reports a queryable that didn't answer.
  See their changelogs.

### Fixed

- **`--timeout` bounds every `fetch`.** A contract with several topics, fetched with no slot given, waited Zenoh's
  default query timeout (10 s) whatever `--timeout` said, and a topic whose queryable didn't answer in time left no
  note: the values the others gave came back as if they were all. Now the timeout bounds the whole fetch, and such a
  topic is noted. Since 0.1.0.
- **Every usage error is JSON.** An error argparse caught (a malformed `--select` or `--until`, a number that isn't
  one, an unknown flag, a missing contract) printed argparse's usage text, not the one JSON line on stderr every
  other error is; the exit code was already 2. Now it's `{"error": "usage", …}`, its message argparse's,
  prefixed with the command (`parsed fetch: argument --select: …`). Since 0.1.0.
- **`--max-bytes`, `--count` and `--timeout` say what they take** when given something that isn't a number, rather
  than naming a private function (`invalid _bytes value`). `--timeout` and `--for` refuse `nan` and `inf`.
- **A malformed `--param` is refused before the session opens.** `query --param noequals` was read only after
  the session opened, so with the router unreachable it exited 5 (`mesh`) where it should exit 2 (`usage`), and
  with one reachable it opened a session to refuse. Since 0.1.0.
- **A refusal says which slot to fill.** A call that left a slot open where the policy allows only a value
  (`fetch FleetState` under a policy that allows one commander's keys, with that slot unbound) was refused with
  only its selector, which read as the contract being off limits. Now it names the slot:
  `…: slot 'owner' is open; give owner=…, or bind it in [bind]`. A slot every allow rule leaves wild isn't named.
  The kind and exit code are unchanged (`refused`, 3).
- **What needs no mesh is checked before the session opens.** `fetch`, `query` and `invoke` opened their session,
  then checked the policy and loaded the request, so with the router down a refused call exited 5 (`mesh`) where it
  should exit 3, an invalid request 5 where it should exit 4, and a write that could reach more than one key 5
  where it should exit 2. With the router up, a session was opened only to refuse. Nothing reached the mesh that
  shouldn't have. Since 0.1.0.

## 0.2.1 — 2026-10-18

**Paths that know their schema, and variables in inject paths.**

### Added

- **Variables in an inject source's path.** A `file` or `toml` path may hold `${NAME}`, or `${NAME:-default}`, the
  default when `NAME` is unset or empty: `toml = "${XDG_CONFIG_HOME:-~/.config}/demo/identity.toml"` reads where a
  client keeps its identity, wherever that is. They expand as the source is read, before `~`. An unset `${NAME}`
  with no default is refused, never read as empty, and a malformed one, or a bare `$NAME`, is refused at load with
  its path. `check` shows the path each source read; `--no-resolve` expands nothing.

### Changed

- **`--select` paths are checked against the contract's schema, before the call.** On `fetch`, `query`, `invoke`,
  `listen` and `wait`, a path that names no field of the reply is refused (exit 2) before anything reaches the
  mesh, so a typo on a write is caught before it's sent, with the names nearest it (`did you mean 'stock'?`). A
  field the schema declares but a value leaves out, such as an optional one, is quiet: no error, and no note. On
  `check`, `contracts` and `describe`, a path must still match something, as before.
- **`wait --until` paths take the same check,** so `--until activty=idle` is refused at once rather than after the
  timeout.
- **`invoke --dry-run` checks `--select` against the reply's schema,** as the real call would, and prints its
  preview whole.

## 0.2.0 — 2026-10-18

**Writes: a contract the policy allows, invoked, and read back.**

### Added

- **`parsed invoke CONTRACT [slot=value …] --request …`** asks a queryable that writes: one that declares `EFFECT =
  WRITE` or `ACTION`, or no effect at all. It's shown only where an allow rule names `invoke`, and `contracts` and
  `describe` list it as the contract's verb. The request is built and checked as `query`'s is, and the replies print
  as a JSON array. A refusal the server sends back is a reply, and exits 0. Once the write is sent, a `--select`
  path that matches nothing is a note, not an error, and replies over the cap say the write was answered, so the
  output never reads as the write not having happened.
- **A write reaches one key.** Every slot of the contract must be given or bound, and none may hold a wildcard, so
  a write can't fan out to every queryable a widened selector would reach. A call that could is refused with exit
  2, naming the slots to give.
- **`parsed invoke --dry-run`** builds the request and checks it and the policy, then prints the selector, the
  variant and the request, each injected secret as `"<injected>"`, without sending anything. A generated field
  shows the value made for the dry run; a real call makes a fresh one.
- **Generated request fields.** `[inject.<field>] generate = "uuid"` fills a field with a fresh uuid4, 32 lowercase
  hex digits, on every call: an id a server can tell a repeat by, such as an order's. Like any injected field it
  reaches every request that declares it, is left out of `describe`, and is refused if given. Unlike a secret, it
  may show in a reply. `check` names its source and makes none.
- **One retry after a lost reply.** A write nothing answered within `--timeout` is asked once more, with the same
  request, when it carries a generated id: a server that tells a repeat by its id answers with the result it gave
  before. A note says it was retried. `--no-retry` turns it off, and a request with no generated id is never
  repeated.
- **A lost write exits 5.** A write still unanswered, after its retry if it had one, is a `mesh` error saying it may
  or may not have been carried out, with a `generated` object holding the request's generated values, so the
  effect can be looked for.
- **`parsed wait CONTRACT [slot=value …] --until PATH=VALUE`** reads back what a write changed, in one call. It
  subscribes to a retained contract or a stream, as `listen` does, and prints the first value that meets every
  `--until`; a retained contract already in that state answers at once. A path is dotted, and a value reads as JSON
  if it can, else as a string. `--timeout` is 30 seconds by default.
- **Exit 7, kind `timeout`:** a `wait` that ran out of time, or was stopped by a signal, with the last value seen in
  `last`, and the message naming any `--until` path no value had.

### Changed

- **An error reply is noted as one.** A server's error reply reads `<Contract> answered with an error: …`, no
  longer as a reply that would not decode, and counts as an answer, so no "no replies" note follows it. Zenoh's
  own `Timeout` reply, for a queryable that outlived the timeout, is no answer: alongside other replies it reads
  "a queryable did not answer within …".
- **The policy comes before the request.** A call's selector, and the tag of its request's root union, are
  checked against the policy before the request loads, so a call the policy refuses exits 3 even when its
  request has faults of its own.

### Fixed

- **A denied variant is refused before its fields are read.** A request whose tag the policy denies was loaded
  first, so a field it lacked or got wrong came back as a validation error naming the field, and only a corrected
  request was refused. It is refused at once now.
- **An unknown tag no longer lists the denied ones.** A request with a tag its union doesn't have, and `describe
  --variant` with one, name only the tags the policy shows. A tag that isn't a string (a list, say) is a validation
  error, not a crash.

## 0.1.1 — 2026-10-18

**A policy checked in CI, without its secrets.**

### Added

- **`parsed check --no-resolve`** checks a policy against its protocol without reading any inject source: the file
  loads, the protocol imports, and every contract, variant tag, bound slot and injected field it names exists. Each
  injected field shows its source, with `"resolved": false`. Where there is no identity file to read, in a
  protocol's own CI say, it checks everything else `check` does; `--connect` still opens the session.

## 0.1.0 — 2026-10-18

**Read-only access to a zeared mesh, typed by its protocol and guarded by a policy file.**

### Added

- **The project:** the `parsed` command (also `python -m parsed`). `--version` names the seared and zeared it runs
  on, since a protocol package parsed reads must pin the same zeared, and `--help` ends with where output goes and
  what each exit code means. The house toolchain gates every commit: `ruff`, `deptry` and `ty` as `prek` hooks, and
  a hook that refuses a commit on `master`. A merge to `master` that changes the version tags the release.
- **Documentation:** the README's install and quick start, and `docs/`: the command (`cli.md`), the policy file
  (`policy.md`), and using parsed from an agent (`agents.md`).
- **The policy file.** `parsed.toml` says which mesh parsed reaches and what it may do there. It is found by
  `--policy` (after the verb), else `$PARSED_POLICY`, else the working directory, and loaded strict, so a misspelt
  key anywhere is refused with its path.
  - `[mesh]` is zeared's own `SessionConfig`, and the only mesh the policy reaches; no flag overrides it.
  - `[protocol] packages` names the protocol packages (or modules) whose contracts it reads.
  - `[bind]` fixes slot values every key renders with, each one concrete chunk.
  - `[inject]` fills a request field from an environment variable, a file, or one value of a TOML file (an array
    of tables picked by matching keys, as a client's identity file keeps its tokens). A value is never printed.
  - `[[allow]]` and `[[deny]]` rules name operations (`read`, `query`, `invoke`, `publish`, `unretain`), contract
    globs, key expressions, and optionally the tags of a request's unions. Nothing is allowed until a rule allows
    it, and deny wins. A name without a glob must name something the protocol has: a contract, a tag, a slot, a
    request field.
- **The catalog.** Each zeared `Message` class of the protocol is a contract: retained, a queryable (it has a
  `REQUEST` or declares an `EFFECT`), a presence feed, or a stream. It is named by its class's name, or by
  `module:Class` when two packages share the name. A queryable that declares no effect counts as mutating.
- **`parsed check`** loads the policy and its protocol, resolves what it injects, and prints what the policy shows,
  by operation, with each injected field's source and length. `--connect` also opens the session.
- **`parsed contracts`** lists every contract the policy shows, one line of each: its kind, the verbs that act on
  it in this version, its topic with the bound slots filled, the slots left to give, and its summary.
- **`parsed describe CONTRACT`** prints one contract: its slots (a bound one shows its value), its effect, the JSON
  Schema of what it takes, and what it gives. The request's schema is seared's load schema, closed, without the
  fields the policy injects or the variants it denies. It is layered, so even a large contract describes in a few
  kilobytes: the reply shows as a field list (each top-level field's name, type and doc), and a request's root union
  folds to its list of tags. `--reply` prints the reply's whole dump schema, `--variant TAG` one variant's schema,
  and the document's `more` names both. A contract the policy hides is refused.
- **`parsed fetch CONTRACT [slot=value …]`** reads a retained contract's values: those the slots select, or every
  one, sorted. A slot left out widens to every value; a bound one is filled by the policy, and given another value
  is refused.
- **`parsed query CONTRACT [slot=value …] [--request JSON]`** asks a queryable that declares `EFFECT = READ` and
  prints its replies, in the order they arrived. The request is the protocol's wire form (`-` reads stdin, `@path`
  a file). The policy's injected fields are added, and it is loaded strict by its own class before anything is sent:
  a value given for an injected field is refused, and seared's error names the field at fault. `--param k=v` adds a
  selector parameter, as a chronicler's history reads take. `--timeout` bounds the wait, 5 s by default.
- **`parsed listen CONTRACT [slot=value …]`** streams a retained contract or a stream, one JSON line per value as it
  arrives: a retained contract's current values first, then live ones. `--meta` adds each value's key, origin
  (`replay` or `live`) and time. It stops after `--count N`, after `--for DURATION` (`30`, `30s`, `5m`, `1h`), or on
  SIGINT or SIGTERM, with exit 0, so under a tool that streams a command's lines (Claude Code's Monitor) it reads
  like a feed. Each sample's key is checked against the slots asked for and against the policy: a sample on a denied
  key is left out and counted, rather than the whole stream refused.
- **Output that fits.** A result over the size cap is refused rather than printed (exit 6), with its size and the
  size of each part, so the way to narrow it shows. The cap is 28,000 bytes by default, just under the 30,000
  characters Claude Code shows of a command's output inline; `--max-bytes` or `$PARSED_MAX_BYTES` moves it, and `0`
  removes it. `--select PATHS` keeps parts of a result, structure kept (`commander.name`, `systems[].key`,
  comma-separated), applied to each item of a list. `--out FILE` writes the whole result to a file and prints where.
  In `listen`, `--select` narrows each line and a line over the cap is left out and noted.
- **`Bytes` fields** print as their length and SHA-256, `{"bytes", "sha256"}`, not their base64.
- **Policy on every read:** the selector a read renders is checked before it is sent. One that could reach a denied
  key is refused, with a hint to narrow the slots, since replies carry no keys to filter by.
- **Notes** go to stderr as `{"note": …}` lines: a reply that would not decode and was left out, no reply at all, a
  sample on a denied key, or samples a stream dropped because its reader fell behind. A library's warning goes to
  stderr as a JSON line too (`{"log": level, "logger", "message"}`), so stderr stays one JSON object a line.
- **Errors** go to stderr as one JSON object, `{"error", "message", "path"}`, and the exit code names the kind: 2
  for the command, the policy file or the protocol; 3 for a call the policy refuses; 4 for a request that does not
  load; 5 for the mesh; 6 for a result over the size cap. Results go to stdout as JSON, indented on a terminal or
  with `--pretty`.
- **Dependencies:** seared `>=0.4.3,<0.5` and zeared `>=0.4.3,<0.5`, pinned to `tag = "v0.4.3"` each.
