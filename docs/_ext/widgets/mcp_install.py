"""MCP install picker widget: 5 MCP clients by 3 install methods."""

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
class Client:
    """One MCP client row in the install picker."""

    id: str
    label: str
    kind: str  # "cli" or "json"
    config_file: str


@dataclass(frozen=True, slots=True)
class Method:
    """One install method (uvx / pipx / pip install)."""

    id: str
    label: str
    doc_url: str | None  # link for the "With [uv] installed:" preamble


@dataclass(frozen=True, slots=True)
class Panel:
    """Pre-built HTML-ready cell for one (client, method) combination."""

    client: Client
    method: Method
    language: str  # "shell" or "json"
    body: str
    is_default: bool


CLIENTS: tuple[Client, ...] = (
    Client(
        id="claude-code",
        label="Claude Code",
        kind="cli",
        config_file=".mcp.json (project) or ~/.claude.json (global)",
    ),
    Client(
        id="claude-desktop",
        label="Claude Desktop",
        kind="json",
        config_file="claude_desktop_config.json",
    ),
    Client(
        id="codex",
        label="Codex CLI",
        kind="cli",
        config_file="~/.codex/config.toml",
    ),
    Client(
        id="gemini",
        label="Gemini CLI",
        kind="cli",
        config_file="~/.gemini/settings.json",
    ),
    Client(
        id="cursor",
        label="Cursor",
        kind="json",
        config_file=".cursor/mcp.json (project) or ~/.cursor/mcp.json (global)",
    ),
)

METHODS: tuple[Method, ...] = (
    Method(id="uvx", label="uvx", doc_url="https://docs.astral.sh/uv/"),
    Method(id="pipx", label="pipx", doc_url="https://pipx.pypa.io/"),
    Method(id="pip", label="pip install", doc_url=None),
)

PIP_PREREQ: str = "pip install --user --upgrade libtmux libtmux-mcp"

_JSON_BODIES: collections.abc.Mapping[str, str] = {
    "uvx": textwrap.dedent(
        """\
        {
            "mcpServers": {
                "libtmux": {
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
                "libtmux": {
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
                "libtmux": {
                    "command": "libtmux-mcp"
                }
            }
        }"""
    ),
}

_CLI_BODIES: collections.abc.Mapping[tuple[str, str], str] = {
    ("claude-code", "uvx"): "claude mcp add libtmux -- uvx libtmux-mcp",
    ("claude-code", "pipx"): "claude mcp add libtmux -- pipx run libtmux-mcp",
    ("claude-code", "pip"): "claude mcp add libtmux -- libtmux-mcp",
    ("codex", "uvx"): "codex mcp add libtmux -- uvx libtmux-mcp",
    ("codex", "pipx"): "codex mcp add libtmux -- pipx run libtmux-mcp",
    ("codex", "pip"): "codex mcp add libtmux -- libtmux-mcp",
    ("gemini", "uvx"): "gemini mcp add libtmux uvx -- libtmux-mcp",
    ("gemini", "pipx"): "gemini mcp add libtmux pipx -- run libtmux-mcp",
    ("gemini", "pip"): "gemini mcp add libtmux libtmux-mcp",
}


def _body_for(client: Client, method: Method) -> str:
    """Return the command / JSON snippet for the given client + method pair."""
    if client.kind == "json":
        return _JSON_BODIES[method.id]
    if client.kind == "cli":
        return _CLI_BODIES[(client.id, method.id)]
    msg = f"unknown client kind: {client.kind!r}"
    raise ValueError(msg)


def build_panels(
    clients: tuple[Client, ...] = CLIENTS,
    methods: tuple[Method, ...] = METHODS,
) -> list[Panel]:
    """Pre-compute every (client, method) panel for template rendering."""
    panels: list[Panel] = []
    for client_index, client in enumerate(clients):
        for method_index, method in enumerate(methods):
            panels.append(
                Panel(
                    client=client,
                    method=method,
                    language="json" if client.kind == "json" else "shell",
                    body=_body_for(client, method),
                    is_default=(client_index == 0 and method_index == 0),
                )
            )
    return panels


class MCPInstallWidget(BaseWidget):
    """MCP client + install-method picker rendered as a single interactive block."""

    name: t.ClassVar[str] = "mcp-install"
    option_spec: t.ClassVar[collections.abc.Mapping[str, t.Any]] = {
        "variant": lambda arg: directives.choice(arg, ("full", "compact")),
    }
    default_options: t.ClassVar[collections.abc.Mapping[str, t.Any]] = {
        "variant": "full",
    }

    @classmethod
    def context(cls, env: BuildEnvironment) -> collections.abc.Mapping[str, t.Any]:
        """Return the clients, methods, pre-built panels, and pip prereq for Jinja."""
        return {
            "clients": CLIENTS,
            "methods": METHODS,
            "panels": build_panels(),
            "pip_prereq": PIP_PREREQ,
        }
