(recipes)=

# Recipes

Each recipe starts from a real workspace situation and traces the agent's
reasoning through discovery, decision, and action. The goal is not to show
tool-call sequences -- it is to show *how an agent decides* what to do with
existing tmux state so you can write better prompts and system instructions.

Every recipe uses the same structure:

- **Situation** -- the developer's world before the agent acts
- **Discover** -- what the agent inspects and why
- **Decide** -- the judgment call that changes the plan
- **Act** -- the minimum safe action sequence
- **The non-obvious part** -- the lesson you would miss from reading tool docs
  alone
- **Prompt** -- a natural-language sentence that triggers this recipe

---

## Find a running dev server and test against it

**Situation.** A developer manages a React project with
[tmuxp](https://tmuxp.git-pull.com). One pane is already running
`pnpm start` with Vite somewhere in the `react` window. They want to run
Playwright e2e tests. The agent does not know which pane has the server,
or what port it chose.

### Discover

```{admonition} Agent reasoning
:class: agent-thought

{toolref}`list-panes` will not help here -- it shows metadata like current
command and working directory, not terminal content. The dev server printed
its URL to the terminal minutes ago, so I need to search terminal content.
```

The agent calls {tooliconl}`search-panes` with `pattern: "Local:"` and
`session_name: "myapp"`. The response comes back with pane `%5` in the `react`
window, matched line: `Local: http://localhost:5173/`.

### Decide

```{admonition} Agent reasoning
:class: agent-thought

The server is alive and its URL is known. I do not need to start anything.
I just need an idle pane for running tests.
```

The agent calls {tooliconl}`list-panes` on the `myapp` session. Several panes show
`pane_current_command: zsh` -- idle shells. It picks `%4` in the same window.

### Act

The agent calls {tooliconl}`send-keys` in pane `%4`:
`PLAYWRIGHT_BASE_URL=http://localhost:5173 pnpm exec playwright test`

Then it calls {tooliconl}`wait-for-text` on pane `%4` with `pattern: "passed|failed|timed out"`, `regex: true`, and `timeout: 120`. Once the
wait resolves, it calls {tooliconl}`capture-pane` on `%4` with `start: -80` to
read the test results.

```{tip}
The agent's first instinct might be to *start* a Vite server. But
{tooliconl}`search-panes` reveals one is already running. This avoids a port
conflict, a wasted pane, and the most common agent mistake: treating tmux
like a blank shell.
```

### The non-obvious part

{toolref}`search-panes` searches terminal *content* -- what you would see on
screen. {toolref}`list-panes` searches *metadata* like current command and
working directory. If the agent had used {toolref}`list-panes` to find a pane
running `node`, it would know a process exists but not whether it is ready or
what URL it chose.

**Prompt:** "Run the Playwright tests against my dev server in the myapp
session."

---

## Start a service and wait for it before running dependent work

**Situation.** The developer is starting fresh in their `backend` session --
no server running yet. They want to run integration tests, but the test
suite needs a live API server.

### Discover

```{admonition} Agent reasoning
:class: agent-thought

First I need to know what exists in the `backend` session. If a server is
already running, I should reuse it instead of starting a duplicate.
```

The agent calls {tooliconl}`list-panes` for the `backend` session. No pane is
running a server process. A {tooliconl}`search-panes` call for `"listening"`
returns no matches.

### Decide

```{admonition} Agent reasoning
:class: agent-thought

Nothing to reuse. I need a dedicated pane for the server so its output
stays separate from the test output.
```

### Act

The agent calls {tooliconl}`split-window` with `session_name: "backend"` to
create a new pane, then calls {tooliconl}`send-keys` in that pane:
`npm run serve`.

The agent calls {tooliconl}`wait-for-text` on the server pane with
`pattern: "Listening on"` and `timeout: 30`. Once the wait resolves, the
agent calls {tooliconl}`send-keys` in the original pane:
`npm test -- --integration`, then {tooliconl}`wait-for-text` with
`pattern: "passed|failed|error"` and `regex: true`, then
{tooliconl}`capture-pane` to read the test results.

```{warning}
Calling {toolref}`capture-pane` immediately after {toolref}`send-keys` is a
race condition. {toolref}`send-keys` returns the moment tmux accepts the
keystrokes, not when the command finishes. Always use {toolref}`wait-for-text`
between them.
```

### The non-obvious part

{toolref}`wait-for-text` replaces `sleep`. The server might start in 2
seconds or 20 -- the agent adapts. The anti-pattern is polling with repeated
{toolref}`capture-pane` calls or hardcoding a sleep duration. The MCP server
handles the polling internally with configurable `timeout` (default 8s) and
`interval` (default 50ms).

**Prompt:** "Start the API server in my backend session and run the
integration tests once it's ready."

---

## Find the failing pane without opening random terminals

**Situation.** The developer kicked off multiple jobs across panes in a `ci`
session -- linting, unit tests, integration tests, type checking. One of
them failed, but they stepped away and do not remember which pane.

### Discover

```{admonition} Agent reasoning
:class: agent-thought

I should not capture every pane and read them all -- that is expensive and
slow. Instead I will search for common failure indicators across all panes
at once.
```

The agent calls {tooliconl}`search-panes` with
`pattern: "FAIL|ERROR|error:|Traceback"`, `regex: true`, scoped to
`session_name: "ci"`.

### Decide

```{admonition} Agent reasoning
:class: agent-thought

Two panes matched: `%3` has `FAIL: test_upload` and `%6` has
`error: Type 'string' is not assignable`. I will capture context from each.
```

### Act

The agent calls {tooliconl}`capture-pane` on `%3` with `start: -60`, then on
`%6` with `start: -60`.

```{tip}
If the error scrolled off the visible screen, use `content_start: -200` (or
deeper) when calling {tooliconl}`search-panes`. The `content_start` parameter
makes search reach into scrollback history, not just the visible screen.
```

### The non-obvious part

{toolref}`search-panes` checks all panes in a single call -- searching 20
panes costs roughly the same as searching 2. An agent that instead calls
{toolref}`list-panes` then {toolref}`capture-pane` on each one individually
makes 20+ round trips for the same information. The `regex: true` parameter
is required here because the `|` in the pattern is a regex alternation, not
literal text.

**Prompt:** "Check my ci session -- which jobs failed?"

---

## Interrupt a stuck process and recover the pane

**Situation.** A long-running build is hanging. The developer wants to
interrupt it, verify the pane is responsive, and re-run the command.

### Discover

```{admonition} Agent reasoning
:class: agent-thought

I need to send Ctrl-C. This is a tmux key name, not text -- so I must use
`enter: false` or tmux will send Ctrl-C followed by Enter, which could
confirm a prompt I did not intend to answer.
```

The agent calls {tooliconl}`send-keys` with `keys: "C-c"` and `enter: false` on
the target pane.

### Decide

```{admonition} Agent reasoning
:class: agent-thought

Did the interrupt work? Some processes ignore {term}`SIGINT`. I will wait briefly
for a shell prompt to reappear. Developers use custom prompts, so I cannot
just look for `$`.
```

The agent calls {tooliconl}`wait-for-text` with `pattern: "[$#>%] *$"`,
`regex: true`, and `timeout: 5`.

```{admonition} Agent reasoning
:class: agent-thought

If the wait resolves, the shell is back. If it times out, the process
ignored Ctrl-C. I will escalate: try {term}`SIGQUIT` (`C-\` with `enter: false`),
then destroy and replace the pane only as a last resort.
```

### Act

If the wait times out, the agent sends `C-\` (also with `enter: false`). If
that also fails, it calls {tooliconl}`kill-pane` on the stuck pane, then
{tooliconl}`split-window` to create a replacement, then {tooliconl}`send-keys` to
re-run.

```{warning}
The `enter: false` parameter is critical. Without it, {toolref}`send-keys`
sends Ctrl-C *then* Enter, which could confirm a "really quit?" prompt,
submit a partially typed command, or enter a newline into a REPL.
```

### The non-obvious part

Recovery is a two-step decision. Try {term}`SIGINT` first (Ctrl-C),
verify it worked with {toolref}`wait-for-text`, escalate to {term}`SIGQUIT`
only if needed. The escalation ladder is: interrupt, verify, escalate signal,
destroy. Skipping
straight to {toolref}`kill-pane` loses the pane's scrollback history and any
partially written output that might explain *why* it hung.

**Prompt:** "The build in pane %2 is stuck. Kill it and restart."

---

## Re-run a command without mixing old and new output

**Situation.** The developer wants `pytest` re-run in tmux, but the
candidate pane already has old test output in scrollback. They want only
fresh results.

### Discover

The agent calls {tooliconl}`list-panes` to find the pane by title, cwd, or
current command. If more than one pane is plausible, it uses
{tooliconl}`capture-pane` with a small range to confirm the target.

### Decide

```{admonition} Agent reasoning
:class: agent-thought

The pane is a shell. I should clear it before running so the capture
afterwards contains only fresh output. If it were running a watcher or
long-lived process, I would not hijack it -- I would use a different pane.
```

### Act

The agent calls {tooliconl}`clear-pane`, then {tooliconl}`send-keys` with
`keys: "pytest"`, then {tooliconl}`wait-for-text` with
`pattern: "passed|failed|error"` and `regex: true`, then
{tooliconl}`capture-pane` to read the fresh output.

### The non-obvious part

{toolref}`clear-pane` runs two tmux commands internally (`send-keys -R` then
`clear-history`) with a brief gap between them. Calling
{toolref}`capture-pane` immediately after {toolref}`clear-pane` may catch
partial state. The {toolref}`wait-for-text` call after {toolref}`send-keys`
naturally provides the needed delay, so the sequence clear-send-wait-capture
is safe.

**Prompt:** "Run `pytest` in the test pane and show me only the fresh
output."

---

## Build a workspace the agent can revisit later

**Situation.** The developer wants a durable project workspace -- not just a
quick split, but a layout that later prompts can refer to by role ("the
server pane", "the test pane").

### Discover

```{admonition} Agent reasoning
:class: agent-thought

Before creating anything, I need to check whether a session with this name
already exists. Creating a duplicate will fail.
```

The agent calls {tooliconl}`list-sessions`. No session named `myproject` exists.

### Decide

```{admonition} Agent reasoning
:class: agent-thought

Safe to create. I need three panes: editor, server, tests. I will create
the session, split twice, then apply a layout so tmux handles the geometry
instead of me calculating sizes.
```

### Act

The agent calls {tooliconl}`create-session` with `session_name: "myproject"` and
`start_directory: "/home/dev/myproject"`. Then {tooliconl}`split-window` twice
(with `direction: "right"` and `direction: "below"`), followed by
{tooliconl}`select-layout` with `layout: "main-vertical"`.

The agent calls {tooliconl}`set-pane-title` on each pane: `editor`, `server`,
`tests`.

The agent calls {tooliconl}`send-keys` in the server pane: `npm run dev`, then
{tooliconl}`wait-for-text` for `pattern: "ready|listening|Local:"` with
`regex: true` and `timeout: 30`.

```{tip}
If the session *does* already exist, the right move is to reuse and extend
it, not recreate it. The {toolref}`list-sessions` check at the top is what
makes that decision possible.
```

### The non-obvious part

Titles and naming are not cosmetic. They reduce future discovery cost. When
the agent comes back in a later conversation and the user says "restart the
server," the agent calls {toolref}`list-panes`, finds the pane titled
`server`, and acts -- no searching, no guessing, no capturing every pane to
figure out which one is which. But note: pane IDs are ephemeral across tmux
server restarts, so the agent should always re-discover by metadata (session
name, pane title, cwd) rather than trusting remembered `%N` values.

**Prompt:** "Set up a tmux workspace for myproject with editor, server, and
test panes."

---

## What to read next

For the principles that recur across these recipes -- discover before acting,
wait instead of polling, content vs. metadata, prefer IDs, escalate
gracefully -- see the {ref}`prompting guide <prompting>`. For specific
pitfalls like `enter: false` and the `send_keys`/`capture_pane` race
condition, see {ref}`gotchas <gotchas>`.

