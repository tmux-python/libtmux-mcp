(troubleshooting)=

# Troubleshooting

Symptom-based guide. Find your problem, follow the steps.

## Server doesn't appear in client

**Symptoms**: Client shows no libtmux tools, or "server not found" errors.

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

**Symptoms**: `list_sessions` returns empty, other tools can't find targets.

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

**Fix**: Use `pane_id` (e.g. `%1`) for unambiguous targeting. Pane IDs are globally unique within a tmux server. Run `list_panes` first to discover IDs.

## Command works in shell but not via MCP

**Symptoms**: `send_keys` sends the command but output isn't what you expect.

**Check**:

1. **Enter key**: `send_keys` sends Enter by default (`enter=true`). If you're sending a partial command, set `enter=false`.

2. **Special characters**: tmux interprets some key names (e.g. `C-c`, `Enter`). If sending literal text, use `literal=true`.

3. **Timing**: After `send_keys`, use `wait_for_text` to wait for the command to complete before capturing output. Don't `capture_pane` immediately — the command may still be running.

## Silent startup failure

**Symptoms**: MCP client says connected but no tools are available.

**Check**:

1. Missing dependency — ensure `fastmcp` is installed:

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

## How to see logs

The MCP server uses Python's `logging` module. To see debug output, set the log level before starting:

```console
$ PYTHONUNBUFFERED=1 uvx libtmux-mcp 2>server.log
```

For Claude Desktop on macOS, MCP server logs are at:
`~/Library/Logs/Claude/mcp-server-libtmux.log`
