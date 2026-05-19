"""MCP install picker widget: client x method x scope x cooldown matrix.

Each MCP client carries its own ``scopes`` tuple (Claude Code has three,
Claude Desktop has one, the rest have two). On top of that, every panel
exists in three **cooldown** modes — ``off`` (no extra flag), ``days``
(insert a ``--exclude-newer`` / ``--uploaded-prior-to`` flag with a
user-configurable day count), and ``bypass`` (skip any global uv
cooldown via ``--no-config`` / ``UV_NO_CONFIG``). Together these
produce 90 server-rendered panels (3 + 1 + 2 + 2 + 2 = 10 scopes,
times 3 methods, times 3 cooldown modes).

Cooldown state is split across three orthogonal axes:
``DEFAULT_COOLDOWN_ENABLED`` (master on/off), ``DEFAULT_COOLDOWN_TYPE``
(``"days"`` vs ``"bypass"``), and ``DEFAULT_COOLDOWN_DAYS`` (day count).
These (along with ``DEFAULT_SCOPES``) are consumed by ``_prehydrate.py``
so Python remains the single source of truth for which (client,
method, scope, cooldown) cell wins on first paint.

Two sentinels appear in days-mode bodies. ``<COOLDOWN_DURATION>`` lands
in uvx + pip bodies and survives as ISO 8601 duration ``P<N>D`` — both
uv and pip 26.1+ re-evaluate the duration on every invocation, so the
flag stays fresh forever once saved in an MCP config. ``<COOLDOWN_DATE>``
lands in pipx bodies because pipx 1.8.0 bundles a pip older than 26.1
that rejects the duration form; JS recomputes the absolute date on
every page load. Both sentinels are swapped by ``cooldown_days_slot``
in ``docs/_ext/widgets/_base.py``.
"""

from __future__ import annotations

import collections.abc
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
    note: str | None


@dataclass(frozen=True, slots=True)
class Cooldown:
    """One cooldown mode (off / days / bypass)."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class Client:
    """One MCP client row in the install picker."""

    id: str
    label: str
    kind: str  # "cli" or "json"
    scopes: tuple[Scope, ...]


@dataclass(frozen=True, slots=True)
class Method:
    """One install method (uvx / pipx / pip install)."""

    id: str
    label: str
    doc_url: str | None


@dataclass(frozen=True, slots=True)
class Panel:
    """Pre-built HTML-ready cell for one (client, method, scope, cooldown)."""

    client: Client
    method: Method
    scope: Scope
    cooldown: Cooldown
    language: str  # "console" | "json" | "toml"
    body: str
    pip_prereq: str | None  # only set for the pip method
    note: str | None  # cooldown-related caveat (e.g. pipx bypass is a no-op)
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


COOLDOWNS: tuple[Cooldown, ...] = (
    Cooldown(id="off", label="Off"),
    Cooldown(id="days", label="Apply a cooldown"),
    Cooldown(id="bypass", label="Bypass global cooldown"),
)


# Default scope per client, derived from the first entry of each ``scopes``
# tuple. Re-exported for ``_prehydrate.py`` so the inline ``<head>`` script
# can fall back to the right default when no scope is saved.
DEFAULT_SCOPES: collections.abc.Mapping[str, str] = {
    client.id: client.scopes[0].id for client in CLIENTS
}

DEFAULT_COOLDOWN_ENABLED: bool = False
DEFAULT_COOLDOWN_TYPE: str = "days"
DEFAULT_COOLDOWN_DAYS: int = 7


# Two sentinels swapped by ``cooldown_days_slot`` in ``_base.py`` after
# Pygments has escaped them to ``&lt;...&gt;``.
#
# * ``<COOLDOWN_DURATION>`` is used by uvx and pip days bodies. uv stores
#   the value as ``ExcludeNewerValue::Relative(ExcludeNewerSpan)`` and
#   re-evaluates ``now - N days`` at every resolver call, so the saved
#   ``.mcp.json`` arg ``"P7D"`` stays fresh forever. pip 26.1+ does the
#   same at flag-parse time on every invocation.
# pipx and pip days panels don't embed a sentinel — they fall back to
# the bare command because pip has no per-package cooldown override
# (see ``_tool_command`` / ``_pip_prereq_for`` / ``_cooldown_note``).
_DURATION_SENTINEL = "<COOLDOWN_DURATION>"


def default_cooldown_date(days: int) -> str:
    """Return the ISO date string for *today (UTC) - days*.

    Used as the build-time default for the cooldown-date slot so the
    server-rendered snippet shows a valid date before JS hydration.
    """
    import datetime as _datetime

    cutoff = _datetime.datetime.now(_datetime.timezone.utc) - _datetime.timedelta(
        days=days
    )
    return cutoff.date().isoformat()


PIP_PREREQ_OFF: str = "pip install --user --upgrade libtmux libtmux-mcp"


def _pip_prereq_for(cooldown: Cooldown) -> str:
    """Return the prereq ``pip install`` line for the given cooldown mode.

    All three modes (off / days / bypass) emit the same bare install
    line. pip's ``--uploaded-prior-to`` has no per-package override,
    so a days-mode cutoff filters fresh libtmux-mcp releases out of
    the resolver — the snippet would fail to install. The per-panel
    ``_cooldown_note`` redirects users to the uvx snippet, which
    carries ``--exclude-newer-package`` for true cooldown enforcement.
    """
    return PIP_PREREQ_OFF


def _tool_command(method: Method, cooldown: Cooldown) -> str:
    """Build the inner ``<tool> [flags] libtmux-mcp`` command.

    This is the portion of every CLI panel that lives *after* the
    ``mcp add ... --`` separator on Claude / Codex / Gemini, and the
    same string that goes into the JSON ``command + args`` pair for
    Claude Desktop / Cursor.
    """
    if method.id == "uvx":
        if cooldown.id == "days":
            # ``--exclude-newer-package libtmux-mcp=2099-01-01`` exempts
            # libtmux-mcp itself from the global cutoff so a recently-
            # released libtmux-mcp stays inside the resolver. Without
            # this, the snippet emits ``no versions of libtmux-mcp``
            # whenever the latest libtmux-mcp release is newer than the
            # cooldown window. pipx + pip have no per-package override,
            # so their days panels carry a caveat note instead (see
            # ``_cooldown_note``).
            return (
                f"uvx --exclude-newer {_DURATION_SENTINEL}"
                " --exclude-newer-package libtmux-mcp=2099-01-01"
                " libtmux-mcp"
            )
        if cooldown.id == "bypass":
            return "uvx --no-config libtmux-mcp"
        return "uvx libtmux-mcp"
    if method.id == "pipx":
        # pipx's pip backend has no per-package cooldown override, and
        # pipx's uv backend doesn't translate ``--uploaded-prior-to`` or
        # ``--exclude-newer`` from ``--pip-args`` (see pipx's
        # ``commands/run_uv.py::_UV_TRANSLATABLE_VALUE_FLAGS``). Both
        # paths fail on fresh libtmux-mcp releases when a days-mode
        # cutoff filters the target package out. All three modes emit
        # the bare ``pipx run`` command; the per-cell ``_cooldown_note``
        # redirects users to the uvx snippet for true cooldown
        # enforcement.
        return "pipx run libtmux-mcp"
    # pip method: same per-package-override limitation. The prereq line
    # (``_pip_prereq_for``) emits the bare ``pip install``; the register
    # step is just ``libtmux-mcp``.
    return "libtmux-mcp"


def _cli_body(client: Client, scope: Scope, method: Method, cooldown: Cooldown) -> str:
    """Build the full shell command for a CLI-kind client."""
    tool_cmd = _tool_command(method, cooldown)
    if client.id == "gemini":
        # gemini's ``--`` separator lands *after* the tool token, not
        # before. Split tool_cmd on the first space so the tool name
        # stays adjacent to the registration slug.
        if " " in tool_cmd:
            head, tail = tool_cmd.split(" ", 1)
            return f"gemini mcp add --scope {scope.id} tmux {head} -- {tail}"
        # pip method: no args, no ``--``.
        return f"gemini mcp add --scope {scope.id} tmux {tool_cmd}"
    # claude-code: ``--scope`` flag added for non-default scopes.
    if client.id == "claude-code":
        flag = "" if scope.id == "local" else f"--scope {scope.id} "
        return f"claude mcp add tmux {flag}-- {tool_cmd}".replace("  ", " ")
    # codex: CLI doesn't write project scope; the project-scope panel
    # uses the TOML body path (see ``_body_for``).
    return f"codex mcp add tmux -- {tool_cmd}"


_JSON_INDENT = "    "


def _json_body(method: Method, cooldown: Cooldown) -> str:
    """Build the JSON config snippet for a JSON-kind client.

    The same JSON works for Claude Desktop and Cursor; the *destination*
    differs (carried on ``Scope.config_file``) but the content does not.
    """
    if method.id == "uvx":
        command = "uvx"
        if cooldown.id == "days":
            # See ``_tool_command`` for the rationale on the
            # ``--exclude-newer-package libtmux-mcp=2099-01-01`` exempt.
            args = (
                f'"--exclude-newer", "{_DURATION_SENTINEL}",'
                ' "--exclude-newer-package", "libtmux-mcp=2099-01-01",'
                ' "libtmux-mcp"'
            )
        else:
            args = '"libtmux-mcp"'
    elif method.id == "pipx":
        # All three modes emit the bare ``pipx run`` args (no
        # ``--pip-args``) — see ``_tool_command`` for the reasoning.
        command = "pipx"
        args = '"run", "libtmux-mcp"'
    else:  # pip
        command = "libtmux-mcp"
        args = None

    # Build server-object members at the 12-space indent (3 levels deep:
    # top → mcpServers → tmux → here). Bypass via ``env`` only applies to
    # uvx (pipx + pip have no global uv cooldown to skip); the per-panel
    # ``note`` field surfaces the no-op caveat on those combos.
    server_indent = _JSON_INDENT * 3
    server_lines = [server_indent + f'"command": "{command}"']
    if args is not None:
        server_lines.append(server_indent + f'"args": [{args}]')
    if cooldown.id == "bypass" and method.id == "uvx":
        server_lines.append(server_indent + '"env": { "UV_NO_CONFIG": "1" }')
    server_block = ",\n".join(server_lines)
    # Avoid ``textwrap.dedent`` here — the f-string-then-dedent pattern
    # collapses when the substituted ``inner`` block lands lines at a
    # smaller indent than the surrounding template, breaking the
    # post-dedent alignment of every member except the first. Explicit
    # per-line indents keep the output stable regardless of source
    # whitespace.
    return (
        "{\n"
        f'{_JSON_INDENT}"mcpServers": {{\n'
        f'{_JSON_INDENT * 2}"tmux": {{\n'
        f"{server_block}\n"
        f"{_JSON_INDENT * 2}}}\n"
        f"{_JSON_INDENT}}}\n"
        "}"
    )


def _toml_body(method: Method, cooldown: Cooldown) -> str:
    """Build the TOML snippet for Codex's project scope (manual edit)."""
    if method.id == "uvx":
        command, args_inner = "uvx", '"libtmux-mcp"'
        if cooldown.id == "days":
            # See ``_tool_command`` for the rationale on the
            # ``--exclude-newer-package libtmux-mcp=2099-01-01`` exempt.
            args_inner = (
                f'"--exclude-newer", "{_DURATION_SENTINEL}",'
                ' "--exclude-newer-package", "libtmux-mcp=2099-01-01",'
                ' "libtmux-mcp"'
            )
    elif method.id == "pipx":
        # All three modes emit the same args — see ``_tool_command`` for
        # the per-package-override rationale.
        command, args_inner = "pipx", '"run", "libtmux-mcp"'
    else:  # pip
        command, args_inner = "libtmux-mcp", None

    lines = [
        "[mcp_servers.tmux]",
        f'command = "{command}"',
    ]
    if args_inner is not None:
        lines.append(f"args = [{args_inner}]")
    if cooldown.id == "bypass" and method.id == "uvx":
        lines.append('env = { UV_NO_CONFIG = "1" }')
    return "\n".join(lines)


def _cooldown_note(method: Method, cooldown: Cooldown) -> str | None:
    """Return a one-line caveat for cells where the snippet has caveats."""
    if method.id in {"pipx", "pip"} and cooldown.id in {"days", "bypass"}:
        # pip's ``--uploaded-prior-to`` has no per-package override (so
        # ``days`` mode would filter the target package out of the
        # resolver for fresh releases) and there's no global cooldown
        # for pip to bypass either. Both modes fall back to the bare
        # command; the note redirects users to the uvx snippet, which
        # carries ``--exclude-newer-package`` for true per-package
        # cooldown enforcement.
        return (
            "pip has no per-package cooldown override, so this snippet"
            " runs without cooldown enforcement. Switch to the uvx tab —"
            " it applies the cooldown to transitive deps via"
            " `--exclude-newer` while exempting libtmux-mcp itself via"
            " `--exclude-newer-package`."
        )
    return None


def _body_for(
    client: Client,
    method: Method,
    scope: Scope,
    cooldown: Cooldown,
) -> tuple[str, str, str | None]:
    """Return ``(body, language, note)`` for one (client, method, scope, cooldown).

    Codex's ``project`` scope is the one cell that escapes its client's
    normal kind — it emits TOML because the Codex CLI doesn't write
    ``project`` scope, so the user pastes manually into a
    ``.codex/config.toml`` at the repo root.
    """
    note = _cooldown_note(method, cooldown)
    if client.id == "codex" and scope.id == "project":
        return _toml_body(method, cooldown), "toml", note
    if client.kind == "json":
        return _json_body(method, cooldown), "json", note
    if client.kind == "cli":
        return _cli_body(client, scope, method, cooldown), "console", note
    msg = f"unknown client kind: {client.kind!r}"
    raise ValueError(msg)


def build_panels(
    clients: tuple[Client, ...] = CLIENTS,
    methods: tuple[Method, ...] = METHODS,
    cooldowns: tuple[Cooldown, ...] = COOLDOWNS,
) -> list[Panel]:
    """Pre-compute every legal (client, method, scope, cooldown) panel."""
    panels: list[Panel] = []
    for client_index, client in enumerate(clients):
        for method_index, method in enumerate(methods):
            for scope_index, scope in enumerate(client.scopes):
                for cooldown_index, cooldown in enumerate(cooldowns):
                    raw, language, note = _body_for(client, method, scope, cooldown)
                    # "console" = BashSessionLexer -- recognises the
                    # leading ``$ `` as Generic.Prompt and emits
                    # ``<span class="gp">``, which the gp-sphinx
                    # copybutton regex strips on copy.
                    body = f"$ {raw}" if language == "console" else raw
                    pip_prereq = (
                        _pip_prereq_for(cooldown) if method.id == "pip" else None
                    )
                    panels.append(
                        Panel(
                            client=client,
                            method=method,
                            scope=scope,
                            cooldown=cooldown,
                            language=language,
                            body=body,
                            pip_prereq=pip_prereq,
                            note=note,
                            is_default=(
                                client_index == 0
                                and method_index == 0
                                and scope_index == 0
                                and cooldown_index == 0
                            ),
                        )
                    )
    return panels


class MCPInstallWidget(BaseWidget):
    """MCP client + install-method + scope + cooldown picker."""

    name: t.ClassVar[str] = "mcp-install"
    option_spec: t.ClassVar[collections.abc.Mapping[str, t.Any]] = {
        "variant": lambda arg: directives.choice(arg, ("full", "compact")),
    }
    default_options: t.ClassVar[collections.abc.Mapping[str, t.Any]] = {
        "variant": "full",
    }

    @classmethod
    def context(cls, env: BuildEnvironment) -> collections.abc.Mapping[str, t.Any]:
        """Return clients, methods, cooldowns, panels, defaults for Jinja."""
        return {
            "clients": CLIENTS,
            "methods": METHODS,
            "cooldowns": COOLDOWNS,
            "panels": build_panels(),
            "default_cooldown_enabled": DEFAULT_COOLDOWN_ENABLED,
            "default_cooldown_type": DEFAULT_COOLDOWN_TYPE,
            "default_cooldown_days": DEFAULT_COOLDOWN_DAYS,
        }
