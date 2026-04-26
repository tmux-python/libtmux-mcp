#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["tomlkit>=0.13"]
# ///
"""Swap MCP server configs across Claude / Codex / Cursor / Gemini CLIs.

Use when you want every installed agent CLI to run a local checkout of an
MCP server (editable) instead of a pinned release. ``use-local`` rewrites
each CLI's config to invoke the checkout via ``uv --directory <repo> run
<entry>``; ``revert`` restores from the timestamped backup the swap wrote.

Defaults are derived from the current repo's ``pyproject.toml``:

- server name = ``project.name`` with a trailing ``-mcp`` stripped
  (``libtmux-mcp`` -> ``libtmux``)
- entry command = first key of ``[project.scripts]``

Examples
--------
```console
$ uv run scripts/mcp_swap.py detect
$ uv run scripts/mcp_swap.py status
$ uv run scripts/mcp_swap.py use-local --dry-run
$ uv run scripts/mcp_swap.py use-local
$ uv run scripts/mcp_swap.py revert
```

Scope
-----
This script is best-effort and intentionally narrow:

- **Global configs only.** Writes to ``~/.cursor/mcp.json``,
  ``~/.claude.json``, ``~/.codex/config.toml``, and
  ``~/.gemini/settings.json``. Workspace / project-local configs
  (``$PWD/.cursor/mcp.json``, ``$PWD/.gemini/settings.json``,
  per-project ``projects.<abs>.mcpServers`` entries inside
  ``~/.claude.json`` *are* recognised for Claude only) are NOT
  walked — workspace files for Cursor/Gemini are silently ignored.
  When workspace precedence matters, run the CLI's own
  ``cursor mcp add ...`` / ``gemini mcp add ...`` directly.
- **Simple binary detection.** Probing is ``shutil.which(<binary>)``
  plus ``<config_path>.exists()``. Custom install locations
  (Homebrew, npm prefixes, ``~/.npm-global/bin``,
  ``~/.claude/local/claude``, ``~/.gemini/local/gemini``) are picked
  up only if the binary is on ``PATH``. FastMCP's installer probes
  these locations directly; this script does not.
- **Single config shape per CLI.** No fallback paths, no merge of
  multiple sources. If your setup deviates from the defaults above,
  use the CLI's native ``mcp`` subcommand instead.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import typing as t

import tomlkit
import tomlkit.items

CLIName = t.Literal["claude", "codex", "cursor", "gemini"]
ALL_CLIS: tuple[CLIName, ...] = ("claude", "codex", "cursor", "gemini")


def _xdg_state_home() -> pathlib.Path:
    """Resolve ``$XDG_STATE_HOME`` per the XDG Base Directory spec.

    Defaults to ``~/.local/state`` when the env var is unset or empty.
    State is the right XDG bucket here (vs. cache / config / data): the
    file is machine-written, must persist across runs so ``revert`` can
    locate the right backup, but is not safely deletable like cache nor
    user-edited like config.
    """
    env = os.environ.get("XDG_STATE_HOME")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".local" / "state"


# ``-dev`` suffix in the namespace makes it loud that this is dev-only
# tooling state, distinct from the runtime ``libtmux-mcp`` package.
STATE_DIR = _xdg_state_home() / "libtmux-mcp-dev" / "swap"
STATE_FILE = STATE_DIR / "state.json"
STATE_VERSION = 1

BACKUP_SUFFIX_PREFIX = ".bak.mcp-swap-"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CLIInfo:
    """Static descriptor for a CLI's config file and discovery heuristics."""

    name: CLIName
    binary: str
    config_path: pathlib.Path
    fmt: t.Literal["json", "toml"]


CLIS: dict[CLIName, CLIInfo] = {
    "claude": CLIInfo(
        name="claude",
        binary="claude",
        config_path=pathlib.Path.home() / ".claude.json",
        fmt="json",
    ),
    "codex": CLIInfo(
        name="codex",
        binary="codex",
        config_path=pathlib.Path.home() / ".codex" / "config.toml",
        fmt="toml",
    ),
    "cursor": CLIInfo(
        name="cursor",
        binary="cursor-agent",
        config_path=pathlib.Path.home() / ".cursor" / "mcp.json",
        fmt="json",
    ),
    "gemini": CLIInfo(
        name="gemini",
        binary="gemini",
        config_path=pathlib.Path.home() / ".gemini" / "settings.json",
        fmt="json",
    ),
}


@dataclasses.dataclass
class McpServerSpec:
    """The portable shape shared across CLI configs."""

    command: str
    args: list[str] = dataclasses.field(default_factory=list)
    env: dict[str, str] = dataclasses.field(default_factory=dict)

    def to_json_dict(self, *, include_stdio_type: bool = False) -> dict[str, t.Any]:
        """Serialize to the JSON shape (Claude-extended when ``include_stdio_type``)."""
        # Claude's format always includes ``type`` and ``env`` (even when empty);
        # Cursor/Gemini omit both. include_stdio_type selects Claude shape.
        if include_stdio_type:
            return {
                "type": "stdio",
                "command": self.command,
                "args": list(self.args),
                "env": dict(self.env),
            }
        out: dict[str, t.Any] = {"command": self.command, "args": list(self.args)}
        if self.env:
            out["env"] = dict(self.env)
        return out

    def is_local_uv_directory(self) -> bool:
        """Return True for a ``uv --directory <repo> run <entry>`` shape."""
        return (
            self.command == "uv" and "--directory" in self.args and "run" in self.args
        )

    def local_repo_path(self) -> pathlib.Path | None:
        """Extract the ``--directory`` argument, if any."""
        try:
            i = self.args.index("--directory")
        except ValueError:
            return None
        if i + 1 >= len(self.args):
            return None
        return pathlib.Path(self.args[i + 1])


@dataclasses.dataclass
class SwapEntry:
    """One CLI's bookkeeping for a swap, written to the state file."""

    config_path: str
    backup_path: str
    server: str
    action: t.Literal["replaced", "added"]


# ---------------------------------------------------------------------------
# Config IO — per format
# ---------------------------------------------------------------------------


def load_config(info: CLIInfo) -> t.Any:
    """Parse a CLI's config file (JSON or TOML) into an editable structure."""
    raw = info.config_path.read_bytes()
    if info.fmt == "json":
        return json.loads(raw)
    return tomlkit.parse(raw.decode())


def dump_config_bytes(info: CLIInfo, config: t.Any) -> bytes:
    """Serialize an edited config back to bytes in its original format."""
    if info.fmt == "json":
        return (json.dumps(config, indent=2) + "\n").encode()
    return tomlkit.dumps(config).encode()


def atomic_write(path: pathlib.Path, data: bytes) -> None:
    """Write bytes to ``path`` via tempfile + ``os.replace`` to avoid partial writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Per-CLI get / set / delete (the only CLI-specific logic)
# ---------------------------------------------------------------------------


@t.overload
def _claude_project_node(
    config: dict[str, t.Any],
    repo: pathlib.Path,
    *,
    create: t.Literal[True],
) -> dict[str, t.Any]: ...


@t.overload
def _claude_project_node(
    config: dict[str, t.Any],
    repo: pathlib.Path,
    *,
    create: t.Literal[False],
) -> dict[str, t.Any] | None: ...


def _claude_project_node(
    config: dict[str, t.Any], repo: pathlib.Path, *, create: bool
) -> dict[str, t.Any] | None:
    """Return (or create) the ``projects.<abs-repo>`` node Claude keys per-project.

    With ``create=True``, the node is unconditionally created if missing
    and the return type is statically narrowed to ``dict[str, t.Any]``;
    callers can drop runtime ``assert node is not None`` defensiveness.
    With ``create=False``, the absence of the node is a real return value
    and the type stays ``dict[str, t.Any] | None``.

    Raises ``RuntimeError`` if Claude's config layout is not the
    expected ``projects.<abs>.mcpServers`` mapping shape — the layout
    is undocumented Claude Code internal state, so a clear error before
    the atomic write beats a silent partial mutation that the backup
    defense would be asked to recover from.
    """
    key = str(repo.resolve())
    projects_node = config.get("projects")
    if projects_node is not None and not isinstance(projects_node, dict):
        msg = (
            "Claude config layout appears to have changed; expected "
            f"'projects' to be a mapping but got "
            f"{type(projects_node).__name__}"
        )
        raise RuntimeError(msg)
    projects = (
        config.setdefault("projects", {}) if create else config.get("projects", {})
    )
    raw_node = projects.get(key)
    node: dict[str, t.Any] | None = None
    if isinstance(raw_node, dict):
        node = raw_node
    elif raw_node is not None:
        msg = (
            "Claude config layout appears to have changed; expected "
            f"'projects[{key!r}]' to be a mapping but got "
            f"{type(raw_node).__name__}"
        )
        raise RuntimeError(msg)
    if node is None and create:
        node = {"allowedTools": [], "mcpContextUris": [], "mcpServers": {}, "env": {}}
        projects[key] = node
    return node


def get_server(
    cli: CLIName, config: t.Any, name: str, repo: pathlib.Path
) -> McpServerSpec | None:
    """Fetch the MCP server entry for ``name`` from a CLI's config, if present."""
    if cli == "claude":
        node = _claude_project_node(config, repo, create=False)
        if not node:
            return None
        entry = node.get("mcpServers", {}).get(name)
    elif cli in ("cursor", "gemini"):
        entry = config.get("mcpServers", {}).get(name)
    else:  # cli == "codex"
        entry = config.get("mcp_servers", {}).get(name)
    if entry is None:
        return None
    return _spec_from_entry(entry, fmt=CLIS[cli].fmt)


def set_server(
    cli: CLIName,
    config: t.Any,
    name: str,
    spec: McpServerSpec,
    repo: pathlib.Path,
) -> t.Literal["replaced", "added"]:
    """Write ``spec`` under ``name`` in a CLI's config, returning replaced/added."""
    if cli == "claude":
        node = _claude_project_node(config, repo, create=True)
        servers = node.setdefault("mcpServers", {})
        had = name in servers
        servers[name] = spec.to_json_dict(include_stdio_type=True)
        return "replaced" if had else "added"
    if cli in ("cursor", "gemini"):
        servers = config.setdefault("mcpServers", {})
        had = name in servers
        servers[name] = spec.to_json_dict()
        return "replaced" if had else "added"
    if cli == "codex":
        # tomlkit: top-level tables are accessed via dict protocol too.
        mcp_servers = config.get("mcp_servers")
        if mcp_servers is None:
            mcp_servers = tomlkit.table()
            config["mcp_servers"] = mcp_servers
        had = name in mcp_servers
        table = tomlkit.table()
        table["command"] = spec.command
        table["args"] = list(spec.args)
        if spec.env:
            env_tbl = tomlkit.table()
            for k, v in spec.env.items():
                env_tbl[k] = v
            table["env"] = env_tbl
        mcp_servers[name] = table
        return "replaced" if had else "added"
    msg = f"unreachable: unknown CLI {cli!r}"
    raise AssertionError(msg)


def delete_server(cli: CLIName, config: t.Any, name: str, repo: pathlib.Path) -> bool:
    """Remove the entry for ``name`` from a CLI's config; return whether it existed."""
    if cli == "claude":
        node = _claude_project_node(config, repo, create=False)
        if not node:
            return False
        servers = node.get("mcpServers", {})
        return servers.pop(name, None) is not None
    if cli in ("cursor", "gemini"):
        return config.get("mcpServers", {}).pop(name, None) is not None
    if cli == "codex":
        mcp_servers = config.get("mcp_servers")
        if mcp_servers is None:
            return False
        if name in mcp_servers:
            del mcp_servers[name]
            return True
        return False
    msg = f"unreachable: unknown CLI {cli!r}"
    raise AssertionError(msg)


def _spec_from_entry(entry: t.Any, *, fmt: t.Literal["json", "toml"]) -> McpServerSpec:
    """Convert a raw config entry (dict or tomlkit Table) into an McpServerSpec."""
    # tomlkit items quack like dicts/lists; coerce to plain Python for our spec.
    if fmt == "toml":
        entry = (
            tomlkit.items.Table.unwrap(entry)
            if isinstance(entry, tomlkit.items.Table)
            else dict(entry)
        )
    command = str(entry.get("command", ""))
    raw_args = entry.get("args", [])
    args = [str(a) for a in raw_args] if raw_args else []
    raw_env = entry.get("env") or {}
    env = {str(k): str(v) for k, v in dict(raw_env).items()}
    return McpServerSpec(command=command, args=args, env=env)


# ---------------------------------------------------------------------------
# Repo metadata
# ---------------------------------------------------------------------------


def resolve_repo_meta(repo: pathlib.Path) -> tuple[str, str]:
    """Derive (server_name, entry_command) from the repo's pyproject.toml."""
    pyproject = repo / "pyproject.toml"
    doc = tomlkit.parse(pyproject.read_text())
    project = doc.get("project")
    if project is None:
        msg = f"{pyproject} has no [project] table"
        raise RuntimeError(msg)
    name = str(project["name"])
    server = name[: -len("-mcp")] if name.endswith("-mcp") else name
    scripts = project.get("scripts") or {}
    if not scripts:
        msg = f"{pyproject} has no [project.scripts] — cannot derive entry"
        raise RuntimeError(msg)
    entry = next(iter(scripts))
    return server, entry


def build_local_spec(repo: pathlib.Path, entry: str) -> McpServerSpec:
    """Build the ``uv --directory <repo> run <entry>`` spec used by ``use-local``."""
    return McpServerSpec(
        command="uv",
        args=["--directory", str(repo.resolve()), "run", entry],
    )


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def load_state() -> dict[CLIName, SwapEntry]:
    """Read the swap-state file, returning an empty mapping when absent."""
    if not STATE_FILE.exists():
        return {}
    raw = json.loads(STATE_FILE.read_text())
    entries = raw.get("entries", {})
    out: dict[CLIName, SwapEntry] = {}
    for k, v in entries.items():
        if k in ALL_CLIS:
            out[t.cast(CLIName, k)] = SwapEntry(**v)
    return out


def save_state(entries: dict[CLIName, SwapEntry]) -> None:
    """Write the swap-state file atomically (versioned payload)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "entries": {k: dataclasses.asdict(v) for k, v in entries.items()},
    }
    atomic_write(STATE_FILE, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))


def clear_state(clis: t.Iterable[CLIName]) -> None:
    """Remove the given CLIs from the state file; delete the file if empty."""
    current = load_state()
    for cli in clis:
        current.pop(cli, None)
    if current:
        save_state(current)
    elif STATE_FILE.exists():
        STATE_FILE.unlink()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Presence:
    """Detection outcome for a CLI: binary on PATH and config file present."""

    cli: CLIName
    binary_found: bool
    config_found: bool

    @property
    def present(self) -> bool:
        """Return True only when both the binary and the config file were found."""
        return self.binary_found and self.config_found


def detect_clis() -> list[Presence]:
    """Probe all supported CLIs and return their detection results."""
    return [
        Presence(
            cli=info.name,
            binary_found=shutil.which(info.binary) is not None,
            config_found=info.config_path.exists(),
        )
        for info in CLIS.values()
    ]


def present_clis() -> list[CLIName]:
    """Return the list of CLIs that have both a binary and a config present."""
    return [p.cli for p in detect_clis() if p.present]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_detect(args: argparse.Namespace) -> int:
    """Print detection results for every supported CLI."""
    for p in detect_clis():
        flag = "yes" if p.present else " no"
        extra = []
        if not p.binary_found:
            extra.append("binary missing")
        if not p.config_found:
            extra.append(f"config missing: {CLIS[p.cli].config_path}")
        suffix = f"  ({', '.join(extra)})" if extra else ""
        print(f"  [{flag}] {p.cli:<7}{suffix}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print the current MCP server entry per detected CLI."""
    repo = pathlib.Path(args.repo).resolve()
    server = args.server or resolve_repo_meta(repo)[0]
    for cli in args.cli or present_clis():
        info = CLIS[cli]
        if not info.config_path.exists():
            print(f"[{cli}] (no config at {info.config_path})")
            continue
        config = load_config(info)
        spec = get_server(cli, config, server, repo)
        if spec is None:
            print(f"[{cli}] no entry for {server!r}")
            continue
        tag = _describe_spec(spec, repo)
        print(f"[{cli}] {server} = {spec.command} {' '.join(spec.args)}  ({tag})")
    return 0


def _describe_spec(spec: McpServerSpec, repo: pathlib.Path) -> str:
    """Return a short label classifying a spec (local/pypi-pin/other)."""
    if spec.is_local_uv_directory():
        local = spec.local_repo_path()
        if local and local.resolve() == repo.resolve():
            return "local: this repo"
        return f"local: {local}"
    if spec.command == "uvx":
        pinned = next((a for a in spec.args if "==" in a or "@" in a), None)
        return f"pypi pin: {pinned}" if pinned else "pypi (unpinned)"
    return "other"


def cmd_use_local(args: argparse.Namespace) -> int:
    """Rewrite each target CLI's config to run the repo's checkout via ``uv``."""
    repo = pathlib.Path(args.repo).resolve()
    server, default_entry = resolve_repo_meta(repo)
    server = args.server or server
    entry = args.entry or default_entry
    spec = build_local_spec(repo, entry)

    targets = args.cli or present_clis()
    if not targets:
        print("no CLIs detected — nothing to do", file=sys.stderr)
        return 1

    ts = time.strftime("%Y%m%d%H%M%S")
    state = load_state()
    had_error = 0
    for cli in targets:
        info = CLIS[cli]
        if not info.config_path.exists():
            print(f"[{cli}] skip — config not found at {info.config_path}")
            continue
        original_bytes = info.config_path.read_bytes()
        config = load_config(info)
        current = get_server(cli, config, server, repo)
        if (
            current
            and current.is_local_uv_directory()
            and current.local_repo_path() == repo
        ):
            print(f"[{cli}] already local (this repo) — no change")
            continue
        # Preserve the existing entry's env on replacement. ``build_local_spec``
        # writes an empty env, so without this merge a swap would silently drop
        # client-side settings (LIBTMUX_SAFETY, LIBTMUX_SOCKET, custom dev
        # knobs). Symmetric with ``_spec_from_entry`` which round-trips env on
        # the read side.
        cli_spec = dataclasses.replace(spec, env={**current.env}) if current else spec
        action = set_server(cli, config, server, cli_spec, repo)
        new_bytes = dump_config_bytes(info, config)

        if args.dry_run:
            print(f"--- {info.config_path} (current)")
            print(f"+++ {info.config_path} (proposed)")
            diff = difflib.unified_diff(
                original_bytes.decode(errors="replace").splitlines(keepends=True),
                new_bytes.decode(errors="replace").splitlines(keepends=True),
                lineterm="",
            )
            sys.stdout.writelines(diff)
            continue

        backup_path = info.config_path.with_suffix(
            info.config_path.suffix + f"{BACKUP_SUFFIX_PREFIX}{ts}"
        )
        backup_path.write_bytes(original_bytes)
        try:
            atomic_write(info.config_path, new_bytes)
            _revalidate(info)
        except Exception as exc:
            atomic_write(info.config_path, original_bytes)
            print(
                f"[{cli}] write failed ({exc}); backup at {backup_path}",
                file=sys.stderr,
            )
            had_error = 1
            continue
        state[cli] = SwapEntry(
            config_path=str(info.config_path),
            backup_path=str(backup_path),
            server=server,
            action=action,
        )
        print(f"[{cli}] {action}; backup: {backup_path}")

    if not args.dry_run:
        save_state(state)
    return had_error


def _revalidate(info: CLIInfo) -> None:
    """Re-parse the file after writing; raise on failure."""
    load_config(info)


def cmd_revert(args: argparse.Namespace) -> int:
    """Restore each target CLI's config from the backup recorded in the state file."""
    state = load_state()
    targets = args.cli or list(state.keys())
    if not targets:
        print("no recorded swaps — nothing to revert", file=sys.stderr)
        return 1

    reverted: list[CLIName] = []
    for cli in targets:
        entry = state.get(cli)
        if entry is None:
            print(f"[{cli}] no state entry — skip")
            continue
        backup = pathlib.Path(entry.backup_path)
        dest = pathlib.Path(entry.config_path)
        if not backup.exists():
            print(f"[{cli}] backup missing: {backup}", file=sys.stderr)
            continue
        if args.dry_run:
            print(f"[{cli}] would restore {dest} from {backup}")
            continue
        atomic_write(dest, backup.read_bytes())
        print(f"[{cli}] restored from {backup}")
        reverted.append(cli)

    if not args.dry_run and reverted:
        clear_state(reverted)
    return 0


# ---------------------------------------------------------------------------
# argparse glue
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``argparse`` parser for ``mcp_swap``."""
    p = argparse.ArgumentParser(prog="mcp_swap", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "detect", help="list installed CLIs and their config presence"
    ).set_defaults(func=cmd_detect)

    ps = sub.add_parser("status", help="show the current MCP server entry per CLI")
    ps.add_argument("--repo", default=".", help="repo root (default: .)")
    ps.add_argument(
        "--server", help="MCP server name (default: derived from pyproject.toml)"
    )
    ps.add_argument(
        "--cli", action="append", choices=ALL_CLIS, help="limit to one or more CLIs"
    )
    ps.set_defaults(func=cmd_status)

    pu = sub.add_parser("use-local", help="rewrite configs to run this checkout")
    pu.add_argument("--repo", default=".", help="repo root (default: .)")
    pu.add_argument(
        "--server", help="MCP server name (default: derived from pyproject.toml)"
    )
    pu.add_argument(
        "--entry", help="uv run entry command (default: [project.scripts] first key)"
    )
    pu.add_argument("--cli", action="append", choices=ALL_CLIS)
    pu.add_argument("--dry-run", action="store_true")
    pu.set_defaults(func=cmd_use_local)

    pr = sub.add_parser("revert", help="restore each CLI's config from its swap backup")
    pr.add_argument("--cli", action="append", choices=ALL_CLIS)
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=cmd_revert)

    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point — dispatches to the selected subcommand."""
    args = build_parser().parse_args(argv)
    return t.cast("int", args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
