(troubleshooting)=

# Troubleshooting

Symptom-based guide. Find your problem, follow the steps.

## Server doesn't appear in client

**Symptoms**: Client shows no tmux MCP tools, or "server not found" errors.

**Check**:

1. Verify the server starts manually:

   ```console
   $ uvx libtmux-mcp
   ```

   You should see no output (it's waiting for stdio input). Press Ctrl+C to stop.

2. Check your client config points to the right command. Common issues:
   - `uvx` not in PATH — [install uv](https://docs.astral.sh/uv/getting-started/installation/)
   - Typo in `"command"` or `"args"` in JSON config
   - TOML config syntax errors (Codex CLI)

3. Restart your MCP client after config changes.

## Tools fail with "no sessions found"

**Symptoms**: {tooliconl}`list-sessions` returns empty, other tools
can't find targets.

**Check**:

1. Is tmux running?

   ```console
   $ tmux list-sessions
   ```

2. Are you on the right socket? If `LIBTMUX_SOCKET` is set, the server only sees sessions on that socket:

   ```console
   $ tmux -L ai_workspace list-sessions
   ```

3. Create a session on the expected socket:

   ```console
   $ tmux -L ai_workspace new-session -d -s test
   ```

## Wrong tmux socket

**Symptoms**: Server sees different sessions than expected, or sees nothing.

**Cause**: `LIBTMUX_SOCKET` in the MCP config isolates the server to a specific socket. Your personal sessions are on the default socket.

**Fix**: Either remove `LIBTMUX_SOCKET` from the config to use the default socket, or ensure sessions exist on the configured socket.

## Pane targeting mismatch

**Symptoms**: Tool targets the wrong pane, or "pane not found" errors.

**Cause**: Using ambiguous targeting (session name + window name) instead of direct IDs.

**Fix**: Use `pane_id` (e.g. `%1`) for unambiguous targeting. Pane IDs
are globally unique within a tmux server. Run {tooliconl}`list-panes`
first to discover IDs.

## Command works in shell but not via MCP

**Symptoms**: {tooliconl}`send-keys` sends the command but output isn't
what you expect.

**Check**:

1. **Enter key**: {tooliconl}`send-keys` sends Enter by default
   (`enter=true`). If you're sending a partial command, set
   `enter=false`.

2. **Special characters**: tmux interprets some key names (e.g. `C-c`, `Enter`). If sending literal text, use `literal=true`.

3. **Timing**: For authored shell commands, prefer {toolref}`run-command`; it waits for completion and returns exit status plus output. Use {toolref}`send-keys` or {toolref}`send-keys-batch` for raw interactive input, {toolref}`capture-since` for repeated observation, and {toolref}`wait-for-text` only when waiting on output you do not author. Don't call {toolref}`capture-pane` immediately after raw input — the command may still be running.

## Silent startup failure

**Symptoms**: MCP client says connected but no tools are available.

**Check**:

1. Missing dependency — ensure [FastMCP](https://gofastmcp.com) is installed:

   ```console
   $ uvx libtmux-mcp
   ```

   If using pip install, check:

   ```console
   $ python -c "import fastmcp; print(fastmcp.__version__)"
   ```

2. Python version — requires 3.10+:

   ```console
   $ python --version
   ```

## Safety tier blocking tools

**Symptoms**: Some tools are missing from the tool list, or return "blocked by safety tier" errors.

**Cause**: `LIBTMUX_SAFETY` is set to a restrictive tier.

**Fix**: Check the configured tier. Default is `mutating`, which includes most tools. Only `destructive` enables kill commands. See {ref}`safety`.

## A wedged tmux server, and a test run that hangs with nothing red

A tmux server can end up *wedged*: the socket accepts connections and
the server never answers. It is not the same as a dead one, and the
difference is what makes it awkward — a dead socket refuses instantly,
which every tool reports correctly, while a wedged one answers nothing
at all.

Every MCP tool is bounded against this at **every** round trip, not
just the first, and refuses in five seconds naming the tmux subcommand
that stalled:

```
tmux list-panes did not return within 5.00s; the tmux server is unresponsive
```

The distinction is the whole difficulty. A tool's liveness probe bounds
its first round trip only, and most tools make several — `break_pane`
makes eleven. A socket that answers the probe and then stalls walks
past the guard, so testing this needs a relay that forwards the first
connection to a real server and stalls the rest. One that never answers
is caught by the probe and reports a clean, confident, useless pass.

What is **not** bounded is a FastMCP `Client` context exiting while
pointed at such a socket: measured, `Client.__aexit__` hangs against a
wedged server and returns cleanly against a healthy or a killed one.

That matters for test suites rather than for the server. A run that
hangs with **no failing test and no output** is the signature — there
is nothing to grep for, because nothing failed.

If you hit it, the cheap first question is whether this machine is
hosting a wedged tmux server. A wedged one burns CPU proportional to
its age; an idle one uses almost none, however old it is:

```console
$ ps -eo pid=,etimes=,cputimes=,comm=,args= \
  | awk '$4=="tmux:" && $5=="server" && $3>10 && $3>$2*0.5 {print $1, $2"s age", $3"s cpu"}'
```

Anything it prints is a candidate; silence means no wedged server here.

Match on `comm` fields, not with `ps -C`. tmux renames the server
process to `tmux: server`, and `ps -C` selects on the command name — so
`ps -C tmux` finds **no tmux servers at all**. Measured on a box
hosting 1,248 of them it returned exactly one row, and that row was an
unrelated shell script that happened to be named `tmux`: a false
negative and a false positive in one command. `ps -C 'tmux: server'`
does not work either, because `-C` cannot match a name containing a
space.

This project's own tests are on the safe side by construction: they
*kill* servers rather than wedging them, and the two that do build a
silent socket never open a `Client` against it.

## How to see logs

The MCP server uses Python's standard {mod}`logging` module. To see debug
output, set the log level before starting:

```console
$ PYTHONUNBUFFERED=1 uvx libtmux-mcp 2>server.log
```

For Claude Desktop on macOS, MCP server logs are at:
`~/Library/Logs/Claude/mcp-server-libtmux.log`
