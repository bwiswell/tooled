# parsed

Typed, policy-guarded access to a [zeared](https://github.com/bwiswell/zeared) mesh, for agents and people.

parsed reads a zeared protocol package and a policy file. Each contract the policy allows becomes something to
fetch, query or listen to, with a JSON Schema, and every call is checked against the policy, and against the
contract's own types, before anything reaches the mesh.

## Why `parsed`

Agents already work against Zenoh meshes, mostly through hand-written CLIs and ad-hoc scripts that open a session
and guess at contract names. parsed generates that surface from the protocol itself:

- **Typed discovery.** The commands come from the protocol's own classes, so a contract or a field that doesn't
  exist is refused with seared's error, which names the argument at fault.
- **Guardrails as configuration.** Which contracts and keys may be touched lives in a strict policy file, not in
  an agent's memory.
- **Output an agent can read.** JSON on stdout, errors as JSON with an exit code per kind, and a size cap that
  refuses a huge result with the size of each part, so the way to narrow it shows.
- **One tool for every zeared mesh,** at no per-project cost beyond the protocol's own documentation.

The policy is a guardrail an agent can't forget, not a security boundary: real enforcement belongs to the mesh's
own access control.

## Install

parsed runs in the same environment as the protocol packages it reads, and they must pin the same zeared (uv
refuses two sources for one package). Install it as a uv tool, with the protocol beside it:

```sh
uv tool install "git+https://github.com/bwiswell/parsed@v0.1.1" --with <protocol-package>
parsed --version            # parsed 0.1.1 (seared 0.4.3, zeared 0.4.3)
```

`--with` takes whatever names the protocol: a path to its checkout, a git URL, or a package name. From a checkout of
parsed, `uv tool install . --with …` does the same.

## Quick start

A protocol package, say `demo.protocol`, with a retained `Station` on `demo/{world}/station/{station}` and a
read queryable `LookReply` on `demo/{world}/look/{player}`. A policy that reads world `w1`:

```toml
# parsed.toml
[mesh]
mode = "client"
router = "tcp/127.0.0.1:7447"

[protocol]
packages = ["demo.protocol"]

[bind]
world = "w1"

[inject.token]
env = "DEMO_TOKEN"

[[allow]]
ops = ["read", "query"]
keys = ["demo/w1/**"]
```

```sh
parsed check --connect                      # what the policy shows; is the mesh there?
parsed contracts                            # one line per contract
parsed describe LookReply                   # its slots, request schema and reply fields
parsed fetch Station                        # every station of w1, sorted
parsed fetch Station station=s2             # one
parsed query LookReply player=ann --request '{"kind": "ping", "station": "s1"}'
parsed listen Station --meta --count 5      # the current set, then live changes
parsed fetch Station --select station,stock
```

## Documentation

- **[docs/cli.md](docs/cli.md)**: the verbs and their options, the output, the size cap, and the exit codes.
- **[docs/policy.md](docs/policy.md)**: the policy file, and how its rules decide what's shown and allowed.
- **[docs/agents.md](docs/agents.md)**: using parsed from Claude Code.

## Development

```sh
uv sync
uv run prek install        # once per clone: the commit hooks
uv run pytest tests/
```

The hooks are the house set (`.pre-commit-config.yaml`): `ruff check`, `ruff format`, `deptry`, `ty check`, the
same gate `seared` and `zeared` run. A fifth refuses any commit on `master`.

**Branches and releases.**
- **`master` holds released commits only,** and moves only by merge.
- **A release is a `version/x.y.z` branch** off `master`.
- **Work on it happens in `feat/…` and `fix/…` branches,** each a pull request squash-merged into the version
  branch. Each adds its own lines to the `unreleased` entry at the top of `CHANGELOG.md`.
- **A minor release then takes a `chore/code-hygiene-and-docs` branch:** docs checked against the code, pins and
  deferred lint swept.
- **Once those are all in, one `chore: version bump` commit** on the version branch bumps the version in
  `pyproject.toml` and `parsed/__init__.py` together (a test checks that they agree), refreshes the lockfile, and
  dates the changelog entry.
- **The version branch then merges into `master`.** There, the `Release Tag` workflow
  (`.github/workflows/release-tag.yml`) tags it `v<version>` and publishes a GitHub release.

Tests mirror the source layout. They serve a small fixture protocol from an in-process loopback Zenoh peer, so
nothing outside the repo is needed.

## Status

0.1.1 reads a mesh: `check`, `contracts`, `describe`, `fetch`, `query` and `listen`, under a strict policy file
with bound slots, injected fields, and allow and deny rules over operations, contracts, keys and union variants.
Not yet: writes (invoking a write or action queryable, publishing), a long-lived `parsed serve` with an MCP face,
and per-protocol extensions.
