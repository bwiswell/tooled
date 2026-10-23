# tooled for agents

tooled is a command an agent runs through its shell. These notes are for Claude Code, where it was shaped, but
apply to any agent with a shell tool.

## Setting it up

- **Install it beside the protocol,** so both share one zeared (see the README), and give the agent a policy file:
  `./tooled.toml` in the working directory, or `TOOLED_POLICY` in the agent's environment.
- **Allow it by verb.** Claude Code's Bash permission rules match a command's prefix, and every tooled verb comes
  first with its options after it, so reads can be allowed by name:

  ```json
  {"permissions": {"allow": ["Bash(tooled check *)", "Bash(tooled operations *)", "Bash(tooled describe *)",
                             "Bash(tooled fetch *)", "Bash(tooled query *)", "Bash(tooled listen *)",
                             "Bash(tooled wait *)"]}}
  ```

  Writes are better allowed one contract at a time (`"Bash(tooled invoke MoveResult *)"`), or left to ask each
  time. Such a rule matches only with the contract right after `invoke`; a command with an option before the
  contract doesn't match, and asks.

  The policy is the guardrail an agent can't forget; the permission rules are the user's own say on each verb.
- **Check it first.** `tooled check --connect` says what the policy shows and whether the mesh answers.
- **Check it in CI, too.** `tooled check --no-resolve --policy agents/player.toml` loads a policy against the
  protocol (every contract, tag, slot and field it names) without reading its secrets, so a renamed contract fails
  the protocol's own tests rather than an agent's session.

## The loop

1. `tooled operations` for what exists, one line each.
2. `tooled describe OPERATION` for one operation's slots, request and reply fields; `--variant TAG` for one variant
   of a request's union, `--reply` for the reply's whole schema.
3. `tooled fetch`, `tooled query` or `tooled wait`. A refusal comes back as JSON with an exit code that says what
   kind (policy 3, the request 4, the mesh 5, size 6, a wait out of time 7), and a request error names the field
   at fault in `path`.

## Writes

1. `tooled describe CONTRACT --variant TAG` for the fields one kind of request takes. The fields the policy
   injects, a token or a generated id, are already left out.
2. `tooled invoke CONTRACT … --dry-run`, when in doubt: the request and the key it would go to, with nothing sent.
3. `tooled invoke CONTRACT … --request '{…}'`. **Read the reply before going on.** A server that refuses a write
   says so in its reply (in many protocols `"ok": false`, with a reason), and tooled exits 0: the refusal is data,
   not a failure.
4. Then `tooled wait` for the state the write should change (`--until activity=idle`). A wait after a refused
   write only runs out its time, which is why the reply comes first.

A write with no reply is asked once more with the same generated id, which a server that tells repeats apart
answers without doing the work twice. If that goes unanswered too, tooled exits 5 with the generated values
(`"generated": {"order_id": …}`): the write may or may not have happened. Look for its effect (a fetch, a log, a
wait) before sending it again, since a write sent again is a new write.

## Long waits

Claude Code's Bash tool waits 2 minutes by default, and 10 at most. A `wait` that may take longer than that runs
as a background command, which tells the session when it ends; or `listen` under Monitor follows the state as it
changes. `--timeout` is how long tooled itself waits: 30 seconds by default.

## Live data

`tooled listen CONTRACT` prints a line per value as it arrives. Run it under Claude Code's Monitor tool and each
line reaches the session as it comes; stop the monitor, and tooled ends cleanly on the signal. `--count` and
`--for` bound it when it runs to completion instead, and `--meta` adds each value's key and whether it was a
replay of retained state or live.

## Keeping output readable

Claude Code shows up to 30,000 characters of a command's output inline; past that it keeps the output in a file
and shows the first 2 KB. tooled's cap sits just under that, at 28,000 bytes, and refuses a bigger result with the
size of each part instead:

- the parts say where the size is; `--select` keeps what's wanted (`--select systems[].key,systems[].name`).
  Its paths are checked against the contract's schema before the call: a guess at a field that doesn't exist is
  refused at once, nothing sent, with the nearest names (`did you mean 'stock'?`); a field that's declared but
  absent from a value is just left out;
- `--out FILE` writes the whole result for reading in pieces;
- `describe` is layered for the same reason: a large reply schema shows as a field list, and a large request union
  as its list of tags.
