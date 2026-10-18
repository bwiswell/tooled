# Changelog

Each version of parsed, newest first.

A **Breaking** line means a command, a flag, an output or a policy file written for the version before may fail, or
behave differently.

The top entry, while it reads *unreleased*, collects what has merged for the next version; each change adds its own
lines as it lands, and the entry is dated when that version is released.

## 0.1.0 — unreleased

**Read-only access to a zeared mesh, typed by its protocol and guarded by a policy file.**

### Added

- **The project:** the `parsed` command (also `python -m parsed`). `--version` names the seared and zeared it runs
  on, since a protocol package parsed reads must pin the same zeared. The house toolchain gates every commit:
  `ruff`, `deptry` and `ty` as `prek` hooks, and a hook that refuses a commit on `master`. A merge to `master` that
  changes the version tags the release.
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
- **Errors** go to stderr as one JSON object, `{"error", "message", "path"}`, and the exit code names the kind: 2
  for the command, the policy file or the protocol; 3 for a call the policy refuses; 5 for the mesh. Results go to
  stdout as JSON, indented on a terminal or with `--pretty`.
- **Dependencies:** seared `>=0.4.3,<0.5` and zeared `>=0.4.3,<0.5`, pinned to `tag = "v0.4.3"` each.
