"""MCP Apps pane viewer: a ``ui://`` page that renders ``snapshot_pane``.

Hosts that speak the MCP Apps extension render this page beside a
``snapshot_pane`` result and let it refresh the snapshot; every other
client ignores the ``_meta.ui`` link and gets the ordinary result.
"""

from __future__ import annotations

import typing as t

from fastmcp.apps import AppConfig, ResourceCSP

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

#: The page's URI, linked from ``snapshot_pane``'s ``AppConfig``.
PANE_VIEWER_URI = "ui://libtmux-mcp/pane-viewer.html"

# Pinned ext-apps SDK; the resource's CSP admits this origin.
_EXT_APPS_ORIGIN = "https://unpkg.com"
_EXT_APPS_MODULE = (
    f"{_EXT_APPS_ORIGIN}/@modelcontextprotocol/ext-apps@2.0.3/app-with-deps"
)

# The page re-runs snapshot_pane every 2s while visible and "live" is checked.
_PANE_VIEWER_HTML = f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<style>
  :root {{ --bg: #fafafa; --fg: #1a1a1a; --dim: #777; --cur: #3572a5; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #111; --fg: #e6e6e6; --dim: #888; --cur: #ffcd23; }}
  }}
  body {{ margin: 0; background: var(--bg); color: var(--fg);
         font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace; }}
  header {{ display: flex; gap: 8px; align-items: center; padding: 6px 8px;
           color: var(--dim); }}
  pre {{ margin: 0; padding: 8px; overflow: auto; white-space: pre; }}
  .cursor {{ background: var(--cur); color: var(--bg); }}
</style>
</head>
<body>
<header>
  <span id="title">waiting for snapshot_pane…</span>
  <label><input id="live" type="checkbox" checked> live</label>
</header>
<pre id="screen"></pre>
<script type="module">
  import {{ App }} from "{_EXT_APPS_MODULE}";

  const app = new App({{ name: "tmux pane viewer", version: "0.1.0" }});
  const screen = document.getElementById("screen");
  const title = document.getElementById("title");
  const live = document.getElementById("live");
  let paneId = null;
  let socketName = null;

  function render(snap) {{
    paneId = snap.pane_id;
    title.textContent = `${{snap.pane_id}} · ${{snap.pane_width}}x${{snap.pane_height}}`
      + (snap.pane_in_mode ? ` · ${{snap.pane_mode}}` : "");
    const rows = snap.content.split("\\n");
    // content drops trailing blank rows; the cursor often sits on one.
    while (rows.length <= snap.cursor_y) rows.push("");
    screen.replaceChildren();
    rows.forEach((row, y) => {{
      if (y === snap.cursor_y) {{
        const x = snap.cursor_x;
        const padded = row.padEnd(x + 1, " ");
        const cur = document.createElement("span");
        cur.className = "cursor";
        cur.textContent = padded[x];
        screen.append(padded.slice(0, x), cur, padded.slice(x + 1));
      }} else {{
        screen.append(row);
      }}
      if (y < rows.length - 1) screen.append("\\n");
    }});
  }}

  function snapshotFrom(result) {{
    if (!result || result.isError) return null;
    return result.structuredContent ?? null;
  }}

  app.ontoolinput = ({{ arguments: args }}) => {{
    socketName = args?.socket_name ?? null;
  }};
  app.ontoolresult = (result) => {{
    const snap = snapshotFrom(result);
    if (snap) render(snap);
  }};

  async function refresh() {{
    if (!paneId || !live.checked || document.hidden) return;
    const args = {{ pane_id: paneId }};
    if (socketName) args.socket_name = socketName;
    const snap = snapshotFrom(
      await app.callServerTool({{ name: "snapshot_pane", arguments: args }}),
    );
    if (snap) render(snap);
  }}

  await app.connect();
  setInterval(() => refresh().catch(() => {{}}), 2000);
</script>
</body>
</html>
"""


def register(mcp: FastMCP) -> None:
    """Register the pane viewer page."""

    @mcp.resource(
        PANE_VIEWER_URI,
        name="pane_viewer",
        title="Pane Viewer",
        description="MCP Apps page that renders and refreshes snapshot_pane.",
        app=AppConfig(csp=ResourceCSP(resource_domains=[_EXT_APPS_ORIGIN])),
    )
    def pane_viewer() -> str:
        """Return the pane viewer page."""
        return _PANE_VIEWER_HTML
