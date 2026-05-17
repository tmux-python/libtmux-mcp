"""MCP install picker widget: 5 MCP clients by 3 install methods by N scopes.

The matrix is **client x method x scope** where scope is per-client (each
client carries its own ``scopes`` tuple). Claude Code has three scopes
(local / user / project), Claude Desktop has one (user), and the rest
have two each — see the per-client ``_*_SCOPES`` constants below.

``DEFAULT_SCOPES`` is derived from ``CLIENTS`` and consumed by
``_prehydrate.py`` so Python remains the single source of truth for
which scope wins on first paint.
"""

from __future__ import annotations

import collections.abc
import textwrap
import typing as t
from dataclasses import dataclass

from docutils.parsers.rst import directives

from ._base import BaseWidget

if t.TYPE_CHECKING:
    from sphinx.environment import BuildEnvironment


@dataclass(frozen=True, slots=True)
class Scope:
    """One config-scope option (e.g. user / project / global)."""

    id: str
    label: str
    config_file: str
    note: str | None  # optional preamble shown above the snippet


@dataclass(frozen=True, slots=True)
class Client:
    """One MCP client row in the install picker."""

    id: str
    label: str
    kind: str  # "cli" or "json"
    scopes: tuple[Scope, ...]  # >=1; first is the SSR default for this client


@dataclass(frozen=True, slots=True)
class Method:
    """One install method (uvx / pipx / pip install)."""

    id: str
    label: str
    doc_url: str | None  # link for the "With [uv] installed:" preamble


@dataclass(frozen=True, slots=True)
class Panel:
    """Pre-built HTML-ready cell for one (client, method, scope) combination."""

    client: Client
    method: Method
    scope: Scope
    language: str  # "console" | "json" | "toml"
    body: str
    is_default: bool


_CLAUDE_CODE_SCOPES: tuple[Scope, ...] = (
    Scope(
        id="local",
        label="Local",
        config_file="~/.claude.json (this project)",
        note=None,
    ),
    Scope(
        id="user",
        label="User",
        config_file="~/.claude.json (all projects)",
        note=None,
    ),
    Scope(
        id="project",
        label="Project",
        config_file=".mcp.json (in repo, version-controlled)",
        note=None,
    ),
)

_CLAUDE_DESKTOP_SCOPES: tuple[Scope, ...] = (
    Scope(
        id="user",
        label="User",
        config_file="claude_desktop_config.json",
        note=None,
    ),
)

_CODEX_SCOPES: tuple[Scope, ...] = (
    Scope(
        id="user",
        label="User",
        config_file="~/.codex/config.toml",
        note=None,
    ),
    Scope(
        id="project",
        label="Project",
        config_file=".codex/config.toml (in repo)",
        note=(
            "Codex's CLI doesn't support project scope yet — paste this"
            " into .codex/config.toml at the repo root."
        ),
    ),
)

_GEMINI_SCOPES: tuple[Scope, ...] = (
    Scope(
        id="user",
        label="User",
        config_file="~/.gemini/settings.json",
        note=None,
    ),
    Scope(
        id="project",
        label="Project",
        config_file=".gemini/settings.json (in repo)",
        note=None,
    ),
)

_CURSOR_SCOPES: tuple[Scope, ...] = (
    Scope(
        id="project",
        label="Project",
        config_file=".cursor/mcp.json (in repo)",
        note=None,
    ),
    Scope(
        id="global",
        label="Global",
        config_file="~/.cursor/mcp.json",
        note=None,
    ),
)


CLIENTS: tuple[Client, ...] = (
    Client(
        id="claude-code",
        label="Claude Code",
        kind="cli",
        scopes=_CLAUDE_CODE_SCOPES,
    ),
    Client(
        id="claude-desktop",
        label="Claude Desktop",
        kind="json",
        scopes=_CLAUDE_DESKTOP_SCOPES,
    ),
    Client(
        id="codex",
        label="Codex CLI",
        kind="cli",
        scopes=_CODEX_SCOPES,
    ),
    Client(
        id="gemini",
        label="Gemini CLI",
        kind="cli",
        scopes=_GEMINI_SCOPES,
    ),
    Client(
        id="cursor",
        label="Cursor",
        kind="json",
        scopes=_CURSOR_SCOPES,
    ),
)


METHODS: tuple[Method, ...] = (
    Method(id="uvx", label="uvx", doc_url="https://docs.astral.sh/uv/"),
    Method(id="pipx", label="pipx", doc_url="https://pipx.pypa.io/"),
    Method(id="pip", label="pip install", doc_url=None),
)


PIP_PREREQ: str = "pip install --user --upgrade libtmux libtmux-mcp"


# Default scope per client, derived from the first entry of each ``scopes``
# tuple. Re-exported for ``_prehydrate.py`` so the inline ``<head>`` script
# can fall back to the right default when no scope is saved.
DEFAULT_SCOPES: collections.abc.Mapping[str, str] = {
    client.id: client.scopes[0].id for client in CLIENTS
}


_JSON_BODIES: collections.abc.Mapping[str, str] = {
    "uvx": textwrap.dedent(
        """\
        {
            "mcpServers": {
                "tmux": {
                    "command": "uvx",
                    "args": ["libtmux-mcp"]
                }
            }
        }"""
    ),
    "pipx": textwrap.dedent(
        """\
        {
            "mcpServers": {
                "tmux": {
                    "command": "pipx",
                    "args": ["run", "libtmux-mcp"]
                }
            }
        }"""
    ),
    "pip": textwrap.dedent(
        """\
        {
            "mcpServers": {
                "tmux": {
                    "command": "libtmux-mcp"
                }
            }
        }"""
    ),
}


_TOML_BODIES: collections.abc.Mapping[str, str] = {
    "uvx": textwrap.dedent(
        """\
        [mcp_servers.tmux]
        command = "uvx"
        args = ["libtmux-mcp"]"""
    ),
    "pipx": textwrap.dedent(
        """\
        [mcp_servers.tmux]
        command = "pipx"
        args = ["run", "libtmux-mcp"]"""
    ),
    "pip": textwrap.dedent(
        """\
        [mcp_servers.tmux]
        command = "libtmux-mcp\""""
    ),
}


_CLI_BODIES: collections.abc.Mapping[tuple[str, str], str] = {
    ("claude-code", "uvx"): "claude mcp add tmux -- uvx libtmux-mcp",
    ("claude-code", "pipx"): "claude mcp add tmux -- pipx run libtmux-mcp",
    ("claude-code", "pip"): "claude mcp add tmux -- libtmux-mcp",
    ("codex", "uvx"): "codex mcp add tmux -- uvx libtmux-mcp",
    ("codex", "pipx"): "codex mcp add tmux -- pipx run libtmux-mcp",
    ("codex", "pip"): "codex mcp add tmux -- libtmux-mcp",
    ("gemini", "uvx"): "gemini mcp add tmux uvx -- libtmux-mcp",
    ("gemini", "pipx"): "gemini mcp add tmux pipx -- run libtmux-mcp",
    ("gemini", "pip"): "gemini mcp add tmux libtmux-mcp",
}


def _with_scope(client_id: str, base: str, scope_id: str) -> str:
    """Insert the ``--scope`` flag into a CLI command at the right position.

    Each CLI has a different syntax for where the flag lands:

    * **claude**: ``claude mcp add tmux --scope X -- <method-cmd>``.
      Skip the flag for ``local`` (claude's default).
    * **gemini**: ``gemini mcp add --scope X tmux <method-cmd>``. Always
      emit the flag — both ``user`` and ``project`` are explicit.
    * **codex**: doesn't support ``--scope`` from the CLI; only the
      default ``user`` scope reaches this code path (codex project
      uses ``_TOML_BODIES`` directly via :func:`_body_for`).
    """
    if client_id == "claude-code":
        if scope_id == "local":
            return base
        return base.replace("mcp add tmux", f"mcp add tmux --scope {scope_id}", 1)
    if client_id == "gemini":
        return base.replace("mcp add tmux", f"mcp add --scope {scope_id} tmux", 1)
    return base


def _body_for(
    client: Client,
    method: Method,
    scope: Scope,
) -> tuple[str, str]:
    """Return ``(body, language)`` for one (client, method, scope) cell.

    Cursor's project vs global cells share an identical JSON body; only
    the config-file label (on :class:`Scope`) differs. Codex project is
    the only cell that escapes its client's normal kind — it shows a TOML
    snippet for manual ``.codex/config.toml`` editing.
    """
    if client.id == "codex" and scope.id == "project":
        return _TOML_BODIES[method.id], "toml"
    if client.kind == "json":
        return _JSON_BODIES[method.id], "json"
    if client.kind == "cli":
        base = _CLI_BODIES[(client.id, method.id)]
        return _with_scope(client.id, base, scope.id), "console"
    msg = f"unknown client kind: {client.kind!r}"
    raise ValueError(msg)


def build_panels(
    clients: tuple[Client, ...] = CLIENTS,
    methods: tuple[Method, ...] = METHODS,
) -> list[Panel]:
    """Pre-compute every legal (client, method, scope) panel for rendering."""
    panels: list[Panel] = []
    for client_index, client in enumerate(clients):
        for method_index, method in enumerate(methods):
            for scope_index, scope in enumerate(client.scopes):
                raw, language = _body_for(client, method, scope)
                # "console" = BashSessionLexer -- recognises the leading
                # ``$ `` as Generic.Prompt and emits ``<span class="gp">``,
                # which the gp-sphinx copybutton regex strips on copy.
                body = f"$ {raw}" if language == "console" else raw
                panels.append(
                    Panel(
                        client=client,
                        method=method,
                        scope=scope,
                        language=language,
                        body=body,
                        is_default=(
                            client_index == 0 and method_index == 0 and scope_index == 0
                        ),
                    )
                )
    return panels


class MCPInstallWidget(BaseWidget):
    """MCP client + install-method + scope picker rendered as one block."""

    name: t.ClassVar[str] = "mcp-install"
    option_spec: t.ClassVar[collections.abc.Mapping[str, t.Any]] = {
        "variant": lambda arg: directives.choice(arg, ("full", "compact")),
    }
    default_options: t.ClassVar[collections.abc.Mapping[str, t.Any]] = {
        "variant": "full",
    }

    @classmethod
    def context(cls, env: BuildEnvironment) -> collections.abc.Mapping[str, t.Any]:
        """Return clients, methods, pre-built panels, and pip prereq for Jinja."""
        return {
            "clients": CLIENTS,
            "methods": METHODS,
            "panels": build_panels(),
            "pip_prereq": PIP_PREREQ,
        }
