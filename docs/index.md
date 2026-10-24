# tooled: docs

tooled gives agents and people typed, policy-guarded access to a [zeared](https://github.com/bwiswell/zeared)
mesh. It reads a protocol package and a policy file; each contract the policy allows becomes something to fetch,
query, listen to, invoke or wait on, checked against the policy and against the contract's own types before
anything reaches the mesh.

- **[cli.md](cli.md)**: the verbs, their options, the output (stdout, stderr, the size cap, `--select`, `--out`),
  and the exit codes.
- **[policy.md](policy.md)**: the policy file: the mesh, the protocol, bound slots, injected fields, and how
  allow and deny rules decide what's shown and what's allowed.
- **[tools.md](tools.md)**: `tooled tools`: the operations a policy shows as tool definitions for a harness, its
  waits and listens among them, in OpenAI's, Anthropic's or MCP's form, in a schema dialect a constrained decoder
  holds.
- **[agents.md](agents.md)**: using tooled from Claude Code: permission rules by verb, the write loop and long
  waits, `listen` under Monitor, and keeping output readable.
