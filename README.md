# parsed

Typed, policy-guarded access to a [zeared](https://github.com/bwiswell/zeared) mesh, for agents and people.

parsed reads a zeared protocol package and a policy file. Each contract the policy allows becomes a command with a
JSON Schema, and every call is checked against the policy, and against the contract's own types, before anything
reaches the mesh.

## Why `parsed`

Agents already work against Zenoh meshes, mostly through hand-written CLIs and ad-hoc scripts that open a session
and guess at contract names. parsed generates that surface from the protocol itself:

- **Typed discovery.** The commands come from the protocol's own classes, so a contract or a field that doesn't
  exist is refused with seared's error, which names the argument at fault.
- **Guardrails as configuration.** Which contracts and keys may be touched lives in a strict policy file, not in
  an agent's memory.
- **One tool for every zeared mesh,** at no per-project cost beyond the protocol's own documentation.

The policy is a guardrail an agent can't forget, not a security boundary: real enforcement belongs to the mesh's
own access control.

## Install

Not released yet. From a checkout:

```sh
uv sync
uv run parsed --version
```

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

Tests mirror the source layout.

## Status

Under construction toward 0.1.0: read-only access to a mesh (`check`, `contracts`, `describe`, `fetch`, `query`,
`listen`), with a policy file deciding what is visible. Today the command answers only `--version`.
