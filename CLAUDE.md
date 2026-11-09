<!-- odin:begin -->
<!-- managed by `odin project init` (guidance v2); edit outside this block -->

## Odin project memory

This project is bound to the Odin namespace `gamemods`. Odin holds durable
project context -- prior decisions, constraints and history -- that is not in
the repository. Use it through the `odin` MCP server's tools:

- Before answering a question about a prior decision, a naming or
  terminology rule, or a constraint not visible in the current files,
  search Odin's project memory first -- even if the user did not mention
  Odin.
- Ordinary coding tasks do not need an Odin call. Do not query it on every
  prompt; the repository is the source of truth for the code itself.
- When Odin has nothing on a subject, say so and use the repository; do not
  invent project history to fill the gap.
- At the end of a task that settled something not recorded in the
  repository -- a decision, a convention, a constraint the operator
  stated -- propose it as a durable memory. Proposals need operator
  approval; nothing is committed for you.
- Never request another project's namespace. This session may only use
  `gamemods`, and any other value is refused.

<!-- odin:end -->
