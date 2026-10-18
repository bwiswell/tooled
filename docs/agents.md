# parsed for agents

parsed is a command an agent runs through its shell. These notes are for Claude Code, where it was shaped, but
apply to any agent with a shell tool.

## Setting it up

- **Install it beside the protocol,** so both share one zeared (see the README), and give the agent a policy file:
  `./parsed.toml` in the working directory, or `PARSED_POLICY` in the agent's environment.
- **Allow it by verb.** Claude Code's Bash permission rules match a command's prefix, and every parsed verb comes
  first with its options after it, so reads can be allowed by name:

  ```json
  {"permissions": {"allow": ["Bash(parsed check *)", "Bash(parsed contracts *)", "Bash(parsed describe *)",
                             "Bash(parsed fetch *)", "Bash(parsed query *)", "Bash(parsed listen *)"]}}
  ```

  The policy is the guardrail an agent can't forget; the permission rules are the user's own say on each verb.
- **Check it first.** `parsed check --connect` says what the policy shows and whether the mesh answers.
- **Check it in CI, too.** `parsed check --no-resolve --policy agents/player.toml` loads a policy against the
  protocol (every contract, tag, slot and field it names) without reading its secrets, so a renamed contract fails
  the protocol's own tests rather than an agent's session.

## The loop

1. `parsed contracts` for what exists, one line each.
2. `parsed describe CONTRACT` for one contract's slots, request and reply fields; `--variant TAG` for one variant
   of a request's union, `--reply` for the reply's whole schema.
3. `parsed fetch` or `parsed query`. A refusal comes back as JSON with an exit code that says what kind
   (policy 3, the request 4, the mesh 5, size 6), and a request error names the field at fault in `path`.

## Live data

`parsed listen CONTRACT` prints a line per value as it arrives. Run it under Claude Code's Monitor tool and each
line reaches the session as it comes; stop the monitor, and parsed ends cleanly on the signal. `--count` and
`--for` bound it when it runs to completion instead, and `--meta` adds each value's key and whether it was a
replay of retained state or live.

## Keeping output readable

Claude Code shows up to 30,000 characters of a command's output inline; past that it keeps the output in a file
and shows the first 2 KB. parsed's cap sits just under that, at 28,000 bytes, and refuses a bigger result with the
size of each part instead:

- the parts say where the size is; `--select` keeps what's wanted (`--select systems[].key,systems[].name`);
- `--out FILE` writes the whole result for reading in pieces;
- `describe` is layered for the same reason: a large reply schema shows as a field list, and a large request union
  as its list of tags.
