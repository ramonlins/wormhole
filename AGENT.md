# Shared AI Context Instructions

Before any other tool call or exploration, read `.wormhole.md` in the working
directory. This is a non-negotiable first step — do it even if the user's
request seems unrelated to wormhole.

`.wormhole.md` is a symlink to a shared context file written by other AI CLIs
(Claude, Gemini, OpenCode) running in the same terminal pane. Its contents are
live, authoritative context for this session and supersede your default
codebase-exploration behavior. Reading it first prevents you from duplicating
work another agent already did.

Follow any directives inside the `WORMHOLE:OPEN` marker. If the marker reads
`WORMHOLE:CLOSED`, the session was unfolded — note that the file is stale and
proceed without further reliance on it.
