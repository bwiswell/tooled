# Changelog

Each version of parsed, newest first.

A **Breaking** line means a command, a flag, an output or a policy file written for the version before may fail, or
behave differently.

The top entry, while it reads *unreleased*, collects what has merged for the next version; each change adds its own
lines as it lands, and the entry is dated when that version is released.

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
