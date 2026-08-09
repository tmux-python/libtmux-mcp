"""Tests for scripts/mcp_swap.py.

The swap script lives outside the ``src/`` package, so we load it via the
module's file path and exercise the round-trip behavior against temporary
config fixtures that mirror each CLI's real layout.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types
import typing as t

import pytest
import tomlkit

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "mcp_swap.py"

_spec = importlib.util.spec_from_file_location("mcp_swap", _SCRIPT)
assert _spec and _spec.loader
mcp_swap = importlib.util.module_from_spec(_spec)
sys.modules["mcp_swap"] = mcp_swap
_spec.loader.exec_module(mcp_swap)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Redirect every config path the script touches into ``tmp_path``."""
    monkeypatch.setattr(
        mcp_swap,
        "CLIS",
        {
            "claude": mcp_swap.CLIInfo(
                name="claude",
                binary="claude",
                config_path=tmp_path / ".claude.json",
                fmt="json",
                container=("mcpServers",),
                dialect="claude",
            ),
            "codex": mcp_swap.CLIInfo(
                name="codex",
                binary="codex",
                config_path=tmp_path / ".codex" / "config.toml",
                fmt="toml",
                container=("mcp_servers",),
                dialect="standard",
            ),
            "cursor": mcp_swap.CLIInfo(
                name="cursor",
                binary="cursor-agent",
                config_path=tmp_path / ".cursor" / "mcp.json",
                fmt="json",
                container=("mcpServers",),
                dialect="standard",
            ),
            "gemini": mcp_swap.CLIInfo(
                name="gemini",
                binary="gemini",
                config_path=tmp_path / ".gemini" / "settings.json",
                fmt="json",
                container=("mcpServers",),
                dialect="standard",
            ),
            "grok": mcp_swap.CLIInfo(
                name="grok",
                binary="grok",
                config_path=tmp_path / ".grok" / "config.toml",
                fmt="toml",
                container=("mcp_servers",),
                dialect="standard",
            ),
            "agy": mcp_swap.CLIInfo(
                name="agy",
                binary="agy",
                config_path=tmp_path / ".gemini" / "config" / "mcp_config.json",
                fmt="json",
                container=("mcpServers",),
                dialect="standard",
            ),
            "opencode": mcp_swap.CLIInfo(
                name="opencode",
                binary="opencode",
                config_path=tmp_path / ".config" / "opencode" / "opencode.jsonc",
                fmt="jsonc",
                container=("mcp",),
                dialect="opencode",
            ),
            "pi": mcp_swap.CLIInfo(
                name="pi",
                binary="pi",
                config_path=tmp_path / ".pi" / "agent" / "mcp.json",
                fmt="json",
                container=("mcpServers",),
                dialect="standard",
            ),
        },
    )
    state_dir = tmp_path / "state"
    monkeypatch.setattr(mcp_swap, "STATE_DIR", state_dir)
    monkeypatch.setattr(mcp_swap, "STATE_FILE", state_dir / "state.json")
    return tmp_path


@pytest.fixture
def fake_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal pyproject.toml repo for meta resolution."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\n"
        'name = "libtmux-mcp"\n'
        "[project.scripts]\n"
        'libtmux-mcp = "libtmux_mcp:main"\n'
    )
    return repo


def _write_json(path: pathlib.Path, data: dict[str, t.Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _pinned_json_entry() -> dict[str, t.Any]:
    return {"command": "uvx", "args": ["libtmux-mcp==0.1.0a2"]}


def _pinned_claude_entry() -> dict[str, t.Any]:
    return {
        "type": "stdio",
        "command": "uvx",
        "args": ["libtmux-mcp==0.1.0a2"],
        "env": {},
    }


# ---------------------------------------------------------------------------
# resolve_repo_meta
# ---------------------------------------------------------------------------


def test_resolve_repo_meta_strips_mcp_suffix(fake_repo: pathlib.Path) -> None:
    """``libtmux-mcp`` resolves to server name ``libtmux`` and entry ``libtmux-mcp``.

    The default matches the slug pre-existing users registered under;
    ``--server tmux`` is the override to target the README/serverInfo
    slug for fresh installs.
    """
    server, entry = mcp_swap.resolve_repo_meta(fake_repo)
    assert server == "libtmux"
    assert entry == "libtmux-mcp"


def test_resolve_repo_meta_uses_name_when_no_suffix(tmp_path: pathlib.Path) -> None:
    """Names without ``-mcp`` suffix pass through unchanged as the server name."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "weather"\n[project.scripts]\nweather = "weather:main"\n'
    )
    assert mcp_swap.resolve_repo_meta(repo) == ("weather", "weather")


# ---------------------------------------------------------------------------
# JSON round-trip: cursor / gemini / agy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cli", ["cursor", "gemini", "agy"])
def test_json_swap_and_revert_round_trip(
    fake_home: pathlib.Path, fake_repo: pathlib.Path, cli: str
) -> None:
    """Swap then revert a JSON-backed CLI must yield byte-identical bytes."""
    info = mcp_swap.CLIS[cli]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    original = info.config_path.read_bytes()

    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", cli]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    after = json.loads(info.config_path.read_text())
    entry = after["mcpServers"]["libtmux"]
    assert entry["command"] == "uv"
    assert entry["args"] == [
        "--directory",
        str(fake_repo.resolve()),
        "run",
        "libtmux-mcp",
    ]

    revert_args = mcp_swap.build_parser().parse_args(["revert", "--cli", cli])
    assert mcp_swap.cmd_revert(revert_args) == 0
    assert info.config_path.read_bytes() == original


def test_grok_and_agy_registered() -> None:
    """The Grok and agy CLIs are exposed as first-class choices."""
    assert "grok" in mcp_swap.ALL_CLIS
    assert "agy" in mcp_swap.ALL_CLIS
    assert mcp_swap.CLIS["grok"].fmt == "toml"
    assert mcp_swap.CLIS["grok"].config_path.name == "config.toml"
    assert mcp_swap.CLIS["agy"].fmt == "json"
    assert mcp_swap.CLIS["agy"].config_path.name == "mcp_config.json"
    parser = mcp_swap.build_parser()
    assert parser.parse_args(["status", "--cli", "grok"]).cli == ["grok"]
    assert parser.parse_args(["status", "--cli", "agy"]).cli == ["agy"]


def test_grok_set_get_delete_roundtrip(fake_repo: pathlib.Path) -> None:
    """The Grok CLI reads/writes the TOML ``[mcp_servers]`` table like Codex."""
    config = tomlkit.parse("")
    spec = mcp_swap.McpServerSpec(
        command="uv", args=["--directory", str(fake_repo), "run", "libtmux-mcp"]
    )
    assert mcp_swap.set_server("grok", config, "libtmux", spec, fake_repo) == "added"
    assert "mcp_servers" in config
    got = mcp_swap.get_server("grok", config, "libtmux", fake_repo)
    assert got is not None
    assert got.is_local_uv_directory()
    assert mcp_swap.set_server("grok", config, "libtmux", spec, fake_repo) == "replaced"
    assert mcp_swap.delete_server("grok", config, "libtmux", fake_repo)
    assert mcp_swap.get_server("grok", config, "libtmux", fake_repo) is None


def test_load_config_tolerates_empty_json(tmp_path: pathlib.Path) -> None:
    """An empty JSON config can be seeded with the first MCP server entry."""
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text("")
    info = mcp_swap.CLIInfo(
        name="agy",
        binary="agy",
        config_path=cfg,
        fmt="json",
        container=("mcpServers",),
        dialect="standard",
    )
    assert mcp_swap.load_config(info) == {}


def test_use_local_preserves_existing_env_when_replacing(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Existing ``env`` on a replaced entry survives ``use-local``.

    Regression: ``cmd_use_local`` previously constructed the replacement
    spec via ``build_local_spec`` (env={}) and wrote it directly,
    silently dropping client-side settings like ``LIBTMUX_SAFETY`` or
    ``LIBTMUX_SOCKET`` that the user had set on the prior pinned-PyPI
    entry. The fix merges ``current.env`` into the new spec; this test
    locks the behaviour by seeding env on a Cursor entry, running
    ``use-local``, and asserting both the new local-uv command shape and
    the original env survived.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {
                "libtmux": {
                    "command": "uvx",
                    "args": ["libtmux-mcp==0.1.0a2"],
                    "env": {"LIBTMUX_SAFETY": "readonly", "FOO": "bar"},
                }
            }
        },
    )

    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "cursor"]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    entry = json.loads(info.config_path.read_text())["mcpServers"]["libtmux"]
    assert entry["command"] == "uv"
    assert entry["args"] == [
        "--directory",
        str(fake_repo.resolve()),
        "run",
        "libtmux-mcp",
    ]
    assert entry["env"] == {"LIBTMUX_SAFETY": "readonly", "FOO": "bar"}


def test_use_local_with_no_prior_entry_writes_empty_env(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """When no prior entry exists, the new spec lands with empty env.

    The env-merge branch only fires for replacements; the "added" path
    (e.g. Codex with no prior libtmux block) should match
    ``build_local_spec``'s default empty env. This pins the Codex add
    case so the merge logic doesn't accidentally synthesise env from
    nothing.
    """
    info = mcp_swap.CLIS["codex"]
    info.config_path.parent.mkdir(parents=True, exist_ok=True)
    info.config_path.write_text("# empty config\n")

    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "codex"]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    config = tomlkit.parse(info.config_path.read_text())
    table = config["mcp_servers"]["libtmux"]
    assert isinstance(table, tomlkit.items.Table)
    assert "env" not in table


def test_json_swap_preserves_unrelated_servers(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Other servers in ``mcpServers`` are not touched during a libtmux swap."""
    info = mcp_swap.CLIS["cursor"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {
                "libtmux": _pinned_json_entry(),
                "agentex": {
                    "command": "uv",
                    "args": ["--directory", "/tmp", "run", "x"],
                },
            }
        },
    )
    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "cursor"]
    )
    assert mcp_swap.cmd_use_local(args) == 0
    after = json.loads(info.config_path.read_text())
    assert set(after["mcpServers"].keys()) == {"libtmux", "agentex"}


# ---------------------------------------------------------------------------
# Claude — per-project keying
# ---------------------------------------------------------------------------


def test_claude_swap_writes_under_repo_abspath_only(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Claude's per-project keying: only this repo's key gets rewritten."""
    info = mcp_swap.CLIS["claude"]
    other_repo_key = "/home/someone/other-project"
    _write_json(
        info.config_path,
        {
            "projects": {
                other_repo_key: {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            }
        },
    )
    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "claude"]
    )
    assert mcp_swap.cmd_use_local(args) == 0
    after = json.loads(info.config_path.read_text())

    assert (
        after["projects"][other_repo_key]["mcpServers"]["libtmux"]
        == _pinned_claude_entry()
    )

    repo_key = str(fake_repo.resolve())
    new_entry = after["projects"][repo_key]["mcpServers"]["libtmux"]
    assert new_entry["type"] == "stdio"
    assert new_entry["command"] == "uv"
    assert new_entry["args"][0:2] == ["--directory", str(fake_repo.resolve())]


# ---------------------------------------------------------------------------
# Claude --scope {user,project}
# ---------------------------------------------------------------------------


def test_claude_user_scope_writes_top_level_mcpServers(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """``--scope user`` rewrites the top-level fallback, not a per-project node."""
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {"mcpServers": {"libtmux": _pinned_claude_entry()}},
    )
    args = mcp_swap.build_parser().parse_args(
        [
            "use-local",
            "--repo",
            str(fake_repo),
            "--cli",
            "claude",
            "--scope",
            "user",
        ]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    after = json.loads(info.config_path.read_text())
    new_entry = after["mcpServers"]["libtmux"]
    assert new_entry["command"] == "uv"
    assert new_entry["args"][0:2] == ["--directory", str(fake_repo.resolve())]
    # No projects.<abs> node should have been created — user scope must
    # not bleed into the per-project layer.
    assert "projects" not in after or str(fake_repo.resolve()) not in after.get(
        "projects", {}
    )


def test_claude_user_scope_round_trip_restores_byte_identical(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """``--scope user`` swap then revert yields byte-identical bytes."""
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {"mcpServers": {"libtmux": _pinned_claude_entry()}},
    )
    original = info.config_path.read_bytes()

    swap_args = mcp_swap.build_parser().parse_args(
        [
            "use-local",
            "--repo",
            str(fake_repo),
            "--cli",
            "claude",
            "--scope",
            "user",
        ]
    )
    assert mcp_swap.cmd_use_local(swap_args) == 0
    assert info.config_path.read_bytes() != original  # sanity

    revert_args = mcp_swap.build_parser().parse_args(
        ["revert", "--cli", "claude", "--scope", "user"]
    )
    assert mcp_swap.cmd_revert(revert_args) == 0
    assert info.config_path.read_bytes() == original


def test_claude_user_and_project_swaps_coexist_independently(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Running both scopes leaves two distinct state entries with separate backups."""
    info = mcp_swap.CLIS["claude"]
    # Seed both layers with PyPI-style entries so the swap has something
    # to replace in each scope.
    _write_json(
        info.config_path,
        {
            "mcpServers": {"libtmux": _pinned_claude_entry()},
            "projects": {
                str(fake_repo.resolve()): {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            },
        },
    )
    parser = mcp_swap.build_parser()

    # First swap: project scope (the legacy default).
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                ["use-local", "--repo", str(fake_repo), "--cli", "claude"]
            )
        )
        == 0
    )
    # Second swap: user scope.
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                [
                    "use-local",
                    "--repo",
                    str(fake_repo),
                    "--cli",
                    "claude",
                    "--scope",
                    "user",
                ]
            )
        )
        == 0
    )

    state = mcp_swap.load_state()
    assert ("claude", "project") in state
    assert ("claude", "user") in state
    assert (
        state[("claude", "project")].backup_path
        != state[("claude", "user")].backup_path
    )

    # Revert just user-scope; project entry must remain intact.
    assert (
        mcp_swap.cmd_revert(
            parser.parse_args(["revert", "--cli", "claude", "--scope", "user"])
        )
        == 0
    )
    state_after = mcp_swap.load_state()
    assert ("claude", "user") not in state_after
    assert ("claude", "project") in state_after

    after = json.loads(info.config_path.read_text())
    # User-level back to PyPI shape.
    assert after["mcpServers"]["libtmux"]["command"] == "uvx"
    # Project-level still local.
    proj_entry = after["projects"][str(fake_repo.resolve())]["mcpServers"]["libtmux"]
    assert proj_entry["command"] == "uv"


def test_claude_full_revert_unwinds_both_scopes_in_lifo_order(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Reverting both Claude scopes (no ``--scope`` filter) restores the original.

    Regression: forward iteration over the swap-chronological state dict
    leaves the file in the post-first-swap state because the second
    backup contains the first swap's modifications. The two backups
    form a layered stack — they must be unwound in reverse-registration
    order (LIFO) so each backup peels off its own layer before the
    prior one is restored. CPython's ``contextlib.ExitStack`` uses the
    same LIFO discipline for the same reason.
    """
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {"libtmux": _pinned_claude_entry()},
            "projects": {
                str(fake_repo.resolve()): {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            },
        },
    )
    original = info.config_path.read_bytes()
    parser = mcp_swap.build_parser()

    # Two swaps in registration order: project first, then user.
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                ["use-local", "--repo", str(fake_repo), "--cli", "claude"]
            )
        )
        == 0
    )
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                [
                    "use-local",
                    "--repo",
                    str(fake_repo),
                    "--cli",
                    "claude",
                    "--scope",
                    "user",
                ]
            )
        )
        == 0
    )

    # Full revert: no --scope filter — must unwind BOTH layers.
    assert mcp_swap.cmd_revert(parser.parse_args(["revert", "--cli", "claude"])) == 0

    # Forward iteration would leave the file in the post-first-swap state
    # (project-scope still local). LIFO restores the true original.
    assert info.config_path.read_bytes() == original
    assert not mcp_swap.STATE_FILE.exists()


def test_use_local_populates_swapped_at_and_seq_no(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """``cmd_use_local`` records both human-readable timestamp and monotonic seq_no."""
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})

    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "cursor"]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    state = mcp_swap.load_state()
    entry = state[("cursor", "user")]
    # ``swapped_at`` is the same ``time.strftime("%Y%m%d%H%M%S")`` value
    # that goes into the backup filename, so checking format suffices.
    assert len(entry.swapped_at) == 14 and entry.swapped_at.isdigit()
    assert entry.swapped_at in entry.backup_path
    # First swap on a clean state starts at zero; subsequent swaps
    # increment.
    assert entry.seq_no == 0


def test_seq_no_increments_across_swaps(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Each new swap gets ``seq_no = max(existing, default=-1) + 1``."""
    info_cursor = mcp_swap.CLIS["cursor"]
    info_gemini = mcp_swap.CLIS["gemini"]
    _write_json(
        info_cursor.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}}
    )
    _write_json(
        info_gemini.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}}
    )
    parser = mcp_swap.build_parser()

    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                ["use-local", "--repo", str(fake_repo), "--cli", "cursor"]
            )
        )
        == 0
    )
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                ["use-local", "--repo", str(fake_repo), "--cli", "gemini"]
            )
        )
        == 0
    )

    state = mcp_swap.load_state()
    assert state[("cursor", "user")].seq_no == 0
    assert state[("gemini", "user")].seq_no == 1


def test_lifo_revert_orders_by_seq_no_not_dict_iteration(
    fake_home: pathlib.Path,
) -> None:
    """LIFO revert sorts by ``seq_no`` regardless of state-file dict order.

    Regression test for the pre-seq_no implementation: the previous
    ``(swapped_at, original_index)`` sort fell back to dict iteration
    order on same-second collisions, and the original ``reversed()``
    approach was dict-order-dependent throughout. This test seeds a
    state file with entries in a *deliberately wrong* dict order —
    higher seq_no first — and asserts the revert still applies the
    higher-seq_no backup first (true LIFO). The explicit ``seq_no``
    field makes the sort independent of dict order, JSON round-trip,
    and wall-clock collisions.
    """
    info = mcp_swap.CLIS["claude"]
    info.config_path.write_text("AFTER_BOTH_SWAPS\n")

    backup_old = mcp_swap.STATE_DIR.parent / "old-backup"
    backup_new = mcp_swap.STATE_DIR.parent / "new-backup"
    backup_old.parent.mkdir(parents=True, exist_ok=True)
    backup_old.write_text("ORIGINAL\n")
    backup_new.write_text("AFTER_FIRST_SWAP\n")

    # Wrong dict order: newer entry (higher seq_no) FIRST in JSON,
    # older entry (lower seq_no) SECOND. Without the explicit-seq_no
    # sort, dict iteration would unwind in the wrong direction.
    mcp_swap.STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_payload = {
        "entries": {
            "claude:user": {
                "config_path": str(info.config_path),
                "backup_path": str(backup_new),
                "server": "libtmux",
                "action": "replaced",
                "swapped_at": "20240202020202",
                "seq_no": 1,  # newer
            },
            "claude:project": {
                "config_path": str(info.config_path),
                "backup_path": str(backup_old),
                "server": "libtmux",
                "action": "replaced",
                "swapped_at": "20240101010101",
                "seq_no": 0,  # older
            },
        },
    }
    mcp_swap.STATE_FILE.write_text(json.dumps(state_payload))

    args = mcp_swap.build_parser().parse_args(["revert", "--cli", "claude"])
    assert mcp_swap.cmd_revert(args) == 0

    # LIFO: seq_no=1 (claude:user) restored first, seq_no=0 (claude:project)
    # restored second. Final file contents = older backup = "ORIGINAL".
    assert info.config_path.read_text() == "ORIGINAL\n"


def test_non_claude_scope_user_passes_through_to_global_config(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """``--scope`` is a no-op for non-Claude CLIs (their config has no scope layer)."""
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})

    # Pass --scope user explicitly: should write the same global entry as
    # if the flag were absent (cursor has no per-project layer).
    args = mcp_swap.build_parser().parse_args(
        [
            "use-local",
            "--repo",
            str(fake_repo),
            "--cli",
            "cursor",
            "--scope",
            "user",
        ]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    after = json.loads(info.config_path.read_text())
    assert after["mcpServers"]["libtmux"]["command"] == "uv"

    # State key reflects the normalised scope, not the raw flag value.
    state = mcp_swap.load_state()
    assert ("cursor", "user") in state
    # And the bizarre case "--scope project" against a non-Claude CLI is
    # silently coerced to user, not stored as a phantom (cursor, project).
    assert ("cursor", "project") not in state


# ---------------------------------------------------------------------------
# Codex TOML — format preservation + add-when-missing
# ---------------------------------------------------------------------------


def test_codex_swap_preserves_toml_comments(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """TOML round-trip preserves top-level comments and sibling tables."""
    info = mcp_swap.CLIS["codex"]
    info.config_path.parent.mkdir(parents=True)
    info.config_path.write_text(
        "# Top-level comment preserved across swap\n"
        "[mcp_servers.libtmux]\n"
        'command = "uvx"\n'
        'args = ["libtmux-mcp==0.1.0a2"]\n'
        "\n"
        "[other]\n"
        "keep = true\n"
    )
    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "codex"]
    )
    assert mcp_swap.cmd_use_local(args) == 0
    text = info.config_path.read_text()
    assert "# Top-level comment preserved across swap" in text
    doc = tomlkit.loads(text).unwrap()
    assert doc["mcp_servers"]["libtmux"]["command"] == "uv"
    assert doc["other"]["keep"] is True


def test_codex_adds_block_when_absent_and_revert_removes_it(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """When no entry exists, ``use-local`` adds one and ``revert`` removes it again."""
    info = mcp_swap.CLIS["codex"]
    info.config_path.parent.mkdir(parents=True)
    info.config_path.write_text("[notice]\nhello = true\n")
    original = info.config_path.read_bytes()

    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "codex"]
    )
    assert mcp_swap.cmd_use_local(args) == 0
    state = mcp_swap.load_state()
    # Codex has no per-project layer, so its scope is always "user".
    assert state[("codex", "user")].action == "added"

    revert_args = mcp_swap.build_parser().parse_args(["revert", "--cli", "codex"])
    assert mcp_swap.cmd_revert(revert_args) == 0
    assert info.config_path.read_bytes() == original


# ---------------------------------------------------------------------------
# Idempotence + dry-run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--dry-run`` prints a diff but leaves the config and state file untouched."""
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    original = info.config_path.read_bytes()

    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "cursor", "--dry-run"]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    assert info.config_path.read_bytes() == original
    assert not mcp_swap.STATE_FILE.exists()
    assert "uv" in capsys.readouterr().out


def test_second_swap_is_noop(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-running ``use-local`` against an already-local config writes nothing new."""
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "cursor"]
    )
    assert mcp_swap.cmd_use_local(args) == 0
    first_bytes = info.config_path.read_bytes()

    capsys.readouterr()
    assert mcp_swap.cmd_use_local(args) == 0
    assert info.config_path.read_bytes() == first_bytes
    assert "already local" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def test_state_file_cleared_after_full_revert(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Reverting every recorded swap deletes the empty state file on disk."""
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    mcp_swap.cmd_use_local(
        mcp_swap.build_parser().parse_args(
            ["use-local", "--repo", str(fake_repo), "--cli", "cursor"]
        )
    )
    assert mcp_swap.STATE_FILE.exists()
    mcp_swap.cmd_revert(mcp_swap.build_parser().parse_args(["revert"]))
    assert not mcp_swap.STATE_FILE.exists()


def test_save_state_writes_atomically(fake_home: pathlib.Path) -> None:
    """save_state routes through atomic_write — no leftover temp files."""
    entry = mcp_swap.SwapEntry(
        config_path="/tmp/cfg.json",
        backup_path="/tmp/cfg.json.bak",
        server="libtmux",
        action="replaced",
        swapped_at="20260101000000",
        seq_no=0,
    )
    mcp_swap.save_state({("claude", "project"): entry})

    assert mcp_swap.STATE_FILE.exists()
    payload = json.loads(mcp_swap.STATE_FILE.read_text())
    assert payload["entries"]["claude:project"]["server"] == "libtmux"

    # tempfile.mkstemp writes siblings prefixed "<name>." — none should
    # remain after a successful atomic_write.
    leftovers = [
        p
        for p in mcp_swap.STATE_DIR.iterdir()
        if p.name.startswith("mcp_swap.json.") and p != mcp_swap.STATE_FILE
    ]
    assert leftovers == [], f"unexpected tempfile leftovers: {leftovers}"


# ---------------------------------------------------------------------------
# McpServerSpec helpers
# ---------------------------------------------------------------------------


def test_is_local_uv_directory_detection() -> None:
    """``McpServerSpec`` shape classification: uv-directory vs uvx-pin."""
    spec = mcp_swap.McpServerSpec(
        command="uv", args=["--directory", "/tmp", "run", "x"]
    )
    assert spec.is_local_uv_directory() is True
    assert spec.local_repo_path() == pathlib.Path("/tmp")

    pypi = mcp_swap.McpServerSpec(command="uvx", args=["libtmux-mcp==0.1.0a2"])
    assert pypi.is_local_uv_directory() is False
    assert pypi.local_repo_path() is None


# ---------------------------------------------------------------------------
# _claude_project_node schema-shape guard
# ---------------------------------------------------------------------------


def test_claude_project_node_rejects_non_mapping_projects(
    fake_repo: pathlib.Path,
) -> None:
    """A non-mapping ``projects`` value is rejected with a clear error.

    Claude's ``~/.claude.json`` layout is undocumented internal state.
    If a future Claude release reshapes ``projects`` (e.g. to a list),
    the script should fail before the atomic write begins so the
    backup defense is not asked to recover from a partially-mutated
    structure.
    """
    config: dict[str, t.Any] = {"projects": "not a dict"}
    with pytest.raises(RuntimeError, match="layout appears to have changed"):
        mcp_swap._claude_project_node(config, fake_repo, create=True)


def test_claude_project_node_rejects_non_mapping_project_node(
    fake_repo: pathlib.Path,
) -> None:
    """A non-mapping per-project node is rejected with a clear error."""
    key = str(fake_repo.resolve())
    config: dict[str, t.Any] = {"projects": {key: "scalar instead of dict"}}
    with pytest.raises(RuntimeError, match="layout appears to have changed"):
        mcp_swap._claude_project_node(config, fake_repo, create=True)


def test_claude_project_node_accepts_well_shaped_config(
    fake_repo: pathlib.Path,
) -> None:
    """Well-shaped config passes through to creation without error."""
    config: dict[str, t.Any] = {}
    node = mcp_swap._claude_project_node(config, fake_repo, create=True)
    assert isinstance(node, dict)
    assert "mcpServers" in node


def test_claude_user_scope_rejects_non_mapping_mcpServers(
    fake_repo: pathlib.Path,
) -> None:
    """User-scope ``set_server`` rejects a non-mapping top-level ``mcpServers``.

    Symmetric with the existing ``_claude_project_node`` shape guard for
    the per-project path. Without this guard, a malformed Claude config
    would surface as an opaque ``AttributeError`` from ``.setdefault()``;
    with it, the user gets the same actionable RuntimeError that the
    project-scope path raises. Pattern follows hatchling's pre-mutation
    config validation in ``builders/config.py``.
    """
    config: dict[str, t.Any] = {"mcpServers": "not a dict"}
    spec = mcp_swap.McpServerSpec(command="uv", args=["run", "libtmux-mcp"])
    with pytest.raises(RuntimeError, match="layout appears to have changed"):
        mcp_swap.set_server("claude", config, "libtmux", spec, fake_repo, scope="user")


def test_claude_user_scope_get_server_rejects_non_mapping_mcpServers(
    fake_repo: pathlib.Path,
) -> None:
    """User-scope ``get_server`` rejects a non-mapping top-level ``mcpServers``.

    Mirrors the write-side guard so reads fail loudly with an actionable
    ``RuntimeError`` instead of an opaque ``AttributeError`` from a
    chained ``.get()``. Symmetric coverage matches the project-scope
    path, which routes all three of read/write/delete through
    ``_claude_project_node``.
    """
    config: dict[str, t.Any] = {"mcpServers": "not a dict"}
    with pytest.raises(RuntimeError, match="layout appears to have changed"):
        mcp_swap.get_server("claude", config, "libtmux", fake_repo, scope="user")


def test_claude_user_scope_delete_server_rejects_non_mapping_mcpServers(
    fake_repo: pathlib.Path,
) -> None:
    """User-scope ``delete_server`` rejects a non-mapping top-level ``mcpServers``.

    Mirrors the write- and read-side guards so deletes fail loudly with
    an actionable ``RuntimeError`` instead of a silent no-op or a
    ``TypeError`` from ``name in servers`` against a non-mapping.
    """
    config: dict[str, t.Any] = {"mcpServers": "not a dict"}
    with pytest.raises(RuntimeError, match="layout appears to have changed"):
        mcp_swap.delete_server("claude", config, "libtmux", fake_repo, scope="user")


# ---------------------------------------------------------------------------
# Graceful CLI error UX — RuntimeError from shape guards must not surface
# as a Python traceback at the CLI boundary.
# ---------------------------------------------------------------------------


def test_use_local_returns_clean_error_on_malformed_claude_user_mcpServers(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed Claude config produces a clean error + exit 1, no traceback.

    Regression: ``set_server``'s shape guard raises ``RuntimeError`` from
    ``_claude_user_servers``, which previously propagated past
    ``cmd_use_local``'s inner ``try/except`` (that one wraps only
    ``atomic_write`` + ``_revalidate``). Per-CLI ``try/except RuntimeError``
    around the config-prep region now catches it. Pattern follows pytest's
    main-level ``UsageError`` formatter in ``_pytest/config/__init__.py``.
    """
    info = mcp_swap.CLIS["claude"]
    info.config_path.parent.mkdir(parents=True, exist_ok=True)
    info.config_path.write_text(json.dumps({"mcpServers": "not a dict"}))

    rc = mcp_swap.main(
        [
            "use-local",
            "--repo",
            str(fake_repo),
            "--cli",
            "claude",
            "--scope",
            "user",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "[claude:user]" in captured.err
    assert "layout appears to have changed" in captured.err
    # No Python traceback should reach the user — only the formatted error.
    assert "Traceback" not in captured.err


def test_status_continues_to_other_clis_on_malformed_claude(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed Claude config does not abort the rest of the status batch.

    Per-CLI continuation: cursor's status line still prints even when
    Claude's config is corrupt. Same per-CLI continuation pattern
    ``cmd_use_local`` and ``cmd_revert`` already use.
    """
    cursor_info = mcp_swap.CLIS["cursor"]
    _write_json(
        cursor_info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}}
    )
    claude_info = mcp_swap.CLIS["claude"]
    claude_info.config_path.parent.mkdir(parents=True, exist_ok=True)
    claude_info.config_path.write_text(json.dumps({"mcpServers": "not a dict"}))

    rc = mcp_swap.main(
        [
            "status",
            "--repo",
            str(fake_repo),
            "--cli",
            "claude",
            "--cli",
            "cursor",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    # Cursor line still printed despite Claude being malformed.
    assert "[cursor]" in captured.out
    # Claude error printed to stderr, not stdout — and no traceback.
    assert "[claude]" in captured.err
    assert "layout appears to have changed" in captured.err
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# State-file resilience — hand-edited corruption must not crash the script.
# Schema is internal (no compat contract) so the policy is "drop on parse
# failure", consistent with how malformed state-file keys already behave.
# ---------------------------------------------------------------------------


def test_load_state_drops_entries_with_non_int_seq_no(
    fake_home: pathlib.Path,
) -> None:
    """A non-coercible ``seq_no`` is dropped at load time.

    Same drop-on-malformed posture as :func:`mcp_swap._parse_state_key`:
    schema is internal, so a hand-edited file with corrupt counter
    values is silently skipped rather than crashing.
    """
    mcp_swap.STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": {
            "claude:user": {
                "config_path": "/tmp/cfg.json",
                "backup_path": "/tmp/cfg.json.bak",
                "server": "libtmux",
                "action": "replaced",
                "swapped_at": "20260101000000",
                "seq_no": "not-an-int",  # corrupted
            },
            "claude:project": {
                "config_path": "/tmp/cfg.json",
                "backup_path": "/tmp/cfg.json.bak2",
                "server": "libtmux",
                "action": "replaced",
                "swapped_at": "20260101000001",
                "seq_no": 1,  # well-formed
            },
        },
    }
    mcp_swap.STATE_FILE.write_text(json.dumps(payload))

    state = mcp_swap.load_state()
    assert ("claude", "user") not in state
    assert ("claude", "project") in state
    assert state[("claude", "project")].seq_no == 1


def test_load_state_coerces_numeric_string_seq_no(
    fake_home: pathlib.Path,
) -> None:
    """A numeric-string ``seq_no`` is coerced via ``int()``, not dropped.

    Distinguishes "user typed quotes around the number" from "user
    typed something non-numeric": the former should still load
    cleanly, the latter should drop.
    """
    mcp_swap.STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": {
            "cursor:user": {
                "config_path": "/tmp/cfg.json",
                "backup_path": "/tmp/cfg.json.bak",
                "server": "libtmux",
                "action": "replaced",
                "swapped_at": "20260101000000",
                "seq_no": "3",  # numeric string — coerce
            },
        },
    }
    mcp_swap.STATE_FILE.write_text(json.dumps(payload))

    state = mcp_swap.load_state()
    assert ("cursor", "user") in state
    assert state[("cursor", "user")].seq_no == 3


def test_load_state_drops_entries_with_missing_required_fields(
    fake_home: pathlib.Path,
) -> None:
    """Entries missing required SwapEntry fields are dropped, not raised.

    Pre-fix, ``SwapEntry(**v)`` raised ``TypeError: missing 1 required
    positional argument: 'seq_no'`` and aborted the load. Post-fix,
    :func:`mcp_swap._parse_state_entry` catches ``TypeError`` and
    returns ``None``, dropping the entry.
    """
    mcp_swap.STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": {
            "cursor:user": {
                # Missing seq_no entirely.
                "config_path": "/tmp/cfg.json",
                "backup_path": "/tmp/cfg.json.bak",
                "server": "libtmux",
                "action": "replaced",
                "swapped_at": "20260101000000",
            },
        },
    }
    mcp_swap.STATE_FILE.write_text(json.dumps(payload))

    state = mcp_swap.load_state()
    assert state == {}


def test_revert_with_corrupt_seq_no_does_not_crash(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
) -> None:
    """Same-CLI two-scope state with one corrupt ``seq_no`` does not raise TypeError.

    Regression: the LIFO sort at ``cmd_revert`` would compare ``int`` vs
    ``str`` (``int < str`` raises in Python 3) when two same-CLI
    entries existed and one had a hand-edited corrupt counter.
    Cross-CLI buckets are length-1 and never invoke comparison —
    making the failure mode asymmetric, only triggering on Claude
    project + user. Validating at load time eliminates the
    asymmetry: the corrupt entry is dropped before it reaches the
    sort, so the well-formed entry's revert applies normally.
    """
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {"libtmux": _pinned_claude_entry()},
            "projects": {
                str(fake_repo.resolve()): {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            },
        },
    )
    parser = mcp_swap.build_parser()
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                ["use-local", "--repo", str(fake_repo), "--cli", "claude"]
            )
        )
        == 0
    )
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                [
                    "use-local",
                    "--repo",
                    str(fake_repo),
                    "--cli",
                    "claude",
                    "--scope",
                    "user",
                ]
            )
        )
        == 0
    )

    # Hand-edit one of the two entries to corrupt seq_no.
    raw = json.loads(mcp_swap.STATE_FILE.read_text())
    raw["entries"]["claude:user"]["seq_no"] = "not-an-int"
    mcp_swap.STATE_FILE.write_text(json.dumps(raw))

    # Revert must NOT raise TypeError. The corrupt entry is silently
    # dropped at load time; the well-formed entry's revert applies.
    rc = mcp_swap.cmd_revert(parser.parse_args(["revert", "--cli", "claude"]))
    assert rc == 0


# ---------------------------------------------------------------------------
# Backup file lifecycle — delete-on-success, keep-on-error.
# ---------------------------------------------------------------------------


def test_revert_deletes_backup_after_successful_restore(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """A successful revert deletes the backup file it just consumed.

    Pre-fix, ``cmd_revert`` restored from ``.bak.mcp-swap-<ts>`` and left
    the file on disk. Repeated swap/revert cycles let backups accumulate
    indefinitely. Post-fix matches CPython's
    ``tempfile.NamedTemporaryFile`` cleanup discipline
    (``Lib/tempfile.py:614-618``): delete on success, keep on error.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    parser = mcp_swap.build_parser()
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                ["use-local", "--repo", str(fake_repo), "--cli", "cursor"]
            )
        )
        == 0
    )
    state = mcp_swap.load_state()
    backup = pathlib.Path(state[("cursor", "user")].backup_path)
    assert backup.exists()

    assert mcp_swap.cmd_revert(parser.parse_args(["revert", "--cli", "cursor"])) == 0
    assert not backup.exists()


def test_revert_dry_run_keeps_backup(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """``revert --dry-run`` must not delete the backup file.

    The dry-run path ``continue``s before reaching the unlink, so this
    locks the behaviour against a future refactor that restructures
    the loop body.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    parser = mcp_swap.build_parser()
    assert (
        mcp_swap.cmd_use_local(
            parser.parse_args(
                ["use-local", "--repo", str(fake_repo), "--cli", "cursor"]
            )
        )
        == 0
    )
    state = mcp_swap.load_state()
    backup = pathlib.Path(state[("cursor", "user")].backup_path)

    assert (
        mcp_swap.cmd_revert(
            parser.parse_args(["revert", "--cli", "cursor", "--dry-run"])
        )
        == 0
    )
    assert backup.exists()


# ---------------------------------------------------------------------------
# `status --scope` filter — completes symmetry with use-local / revert.
# ---------------------------------------------------------------------------


def test_status_scope_user_filters_to_user_only(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``status --scope user`` shows only the user-scope claude line."""
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {"libtmux": _pinned_claude_entry()},
            "projects": {
                str(fake_repo.resolve()): {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            },
        },
    )
    args = mcp_swap.build_parser().parse_args(
        [
            "status",
            "--repo",
            str(fake_repo),
            "--cli",
            "claude",
            "--scope",
            "user",
        ]
    )
    assert mcp_swap.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "[claude:user]" in out
    assert "[claude:project]" not in out


def test_status_scope_project_filters_to_project_only(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``status --scope project`` shows only the project-scope claude line."""
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {"libtmux": _pinned_claude_entry()},
            "projects": {
                str(fake_repo.resolve()): {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            },
        },
    )
    args = mcp_swap.build_parser().parse_args(
        [
            "status",
            "--repo",
            str(fake_repo),
            "--cli",
            "claude",
            "--scope",
            "project",
        ]
    )
    assert mcp_swap.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "[claude:project]" in out
    assert "[claude:user]" not in out


def test_status_no_scope_shows_both_layers(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without ``--scope``, both Claude layers print when both have entries.

    Locks the existing default behaviour so a future refactor can't
    silently change it.
    """
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {"libtmux": _pinned_claude_entry()},
            "projects": {
                str(fake_repo.resolve()): {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            },
        },
    )
    args = mcp_swap.build_parser().parse_args(
        ["status", "--repo", str(fake_repo), "--cli", "claude"]
    )
    assert mcp_swap.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "[claude:user]" in out
    assert "[claude:project]" in out


def test_status_scope_no_op_for_non_claude_cli(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--scope`` is a no-op for non-Claude CLIs (their config has no scope layer).

    Asserts that ``--cli cursor --scope project`` produces the same
    single ``[cursor]`` line as ``--cli cursor`` alone.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    parser = mcp_swap.build_parser()

    # Without --scope.
    assert (
        mcp_swap.cmd_status(
            parser.parse_args(["status", "--repo", str(fake_repo), "--cli", "cursor"])
        )
        == 0
    )
    out_no_scope = capsys.readouterr().out

    # With --scope project (silently no-op for cursor).
    assert (
        mcp_swap.cmd_status(
            parser.parse_args(
                [
                    "status",
                    "--repo",
                    str(fake_repo),
                    "--cli",
                    "cursor",
                    "--scope",
                    "project",
                ]
            )
        )
        == 0
    )
    out_with_scope = capsys.readouterr().out

    assert out_no_scope == out_with_scope
    assert "[cursor]" in out_with_scope


def test_status_scope_user_with_only_project_entry_shows_no_entry(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Filtering to a scope with no entry prints a scope-tagged "no entry" line.

    Locks the symmetry with ``use-local`` / ``revert`` output, which
    label scope-filtered actions as ``[claude:<scope>]`` rather than
    falling back to the unscoped ``[claude]`` form.
    """
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {
            "projects": {
                str(fake_repo.resolve()): {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            },
        },
    )
    args = mcp_swap.build_parser().parse_args(
        [
            "status",
            "--repo",
            str(fake_repo),
            "--cli",
            "claude",
            "--scope",
            "user",
        ]
    )
    assert mcp_swap.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "[claude:user] no entry for" in out
    assert "[claude:project]" not in out


# ---------------------------------------------------------------------------
# use-local --env injection
# ---------------------------------------------------------------------------


def _local_entry(repo: pathlib.Path) -> dict[str, t.Any]:
    """Return a local ``uv --directory <repo> run`` JSON entry (use-local shape)."""
    return {
        "command": "uv",
        "args": ["--directory", str(repo.resolve()), "run", "libtmux-mcp"],
    }


def test_use_local_env_flag_injects_into_entry(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """``--env KEY=VALUE`` lands in the written server entry's ``env``.

    The isolation workflow needs to point the server at a scratch socket
    without a manual post-edit; ``--env`` writes that env at swap time.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {}})

    args = mcp_swap.build_parser().parse_args(
        [
            "use-local",
            "--repo",
            str(fake_repo),
            "--cli",
            "cursor",
            "--env",
            "LIBTMUX_SOCKET=mcp-target",
        ]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    entry = json.loads(info.config_path.read_text())["mcpServers"]["libtmux"]
    assert entry["env"] == {"LIBTMUX_SOCKET": "mcp-target"}


def test_use_local_env_flag_wins_over_preserved_env(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Explicit ``--env`` overrides a preserved key; other preserved keys survive."""
    info = mcp_swap.CLIS["cursor"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {
                "libtmux": {
                    "command": "uvx",
                    "args": ["libtmux-mcp==0.1.0a2"],
                    "env": {"LIBTMUX_SAFETY": "readonly", "KEEP": "me"},
                }
            }
        },
    )

    args = mcp_swap.build_parser().parse_args(
        [
            "use-local",
            "--repo",
            str(fake_repo),
            "--cli",
            "cursor",
            "--env",
            "LIBTMUX_SAFETY=destructive",
        ]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    entry = json.loads(info.config_path.read_text())["mcpServers"]["libtmux"]
    assert entry["env"] == {"LIBTMUX_SAFETY": "destructive", "KEEP": "me"}


def test_env_pair_rejects_malformed() -> None:
    """``--env`` without ``=`` is an argparse error, not a silent skip."""
    with pytest.raises(SystemExit):
        mcp_swap.build_parser().parse_args(["use-local", "--env", "NOEQUALS"])


def test_use_local_env_written_on_already_local_entry(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """``--env`` still writes when the entry already points at this repo.

    Regression: the already-local short-circuit ``continue``d before the env
    merge, so ``--env`` (e.g. an isolated ``LIBTMUX_SOCKET``) was silently
    dropped whenever the config already pointed local — the common case. The
    guard now only short-circuits when the requested env is already satisfied.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _local_entry(fake_repo)}})

    args = mcp_swap.build_parser().parse_args(
        [
            "use-local",
            "--repo",
            str(fake_repo),
            "--cli",
            "cursor",
            "--env",
            "LIBTMUX_SOCKET=mcp-target",
        ]
    )
    assert mcp_swap.cmd_use_local(args) == 0

    entry = json.loads(info.config_path.read_text())["mcpServers"]["libtmux"]
    assert entry.get("env") == {"LIBTMUX_SOCKET": "mcp-target"}


def test_use_local_already_local_still_noop_when_env_matches(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """The short-circuit still fires when the requested env is already present."""
    info = mcp_swap.CLIS["cursor"]
    spec = _local_entry(fake_repo)
    spec["env"] = {"LIBTMUX_SOCKET": "mcp-target"}
    _write_json(info.config_path, {"mcpServers": {"libtmux": spec}})
    before = info.config_path.read_bytes()

    args = mcp_swap.build_parser().parse_args(
        [
            "use-local",
            "--repo",
            str(fake_repo),
            "--cli",
            "cursor",
            "--env",
            "LIBTMUX_SOCKET=mcp-target",
        ]
    )
    assert mcp_swap.cmd_use_local(args) == 0
    assert info.config_path.read_bytes() == before


# ---------------------------------------------------------------------------
# naming hint
# ---------------------------------------------------------------------------


def test_naming_hint_points_at_registered_alias(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Hint names the real slug when the repo uses a non-default server name.

    A bare run would otherwise no-op on a missing entry, so the hint points
    at the name the CLIs were actually registered under.
    """
    _write_json(
        mcp_swap.CLIS["cursor"].config_path,
        {"mcpServers": {"tmux": _local_entry(fake_repo)}},
    )
    hint = mcp_swap._naming_hint(fake_repo.resolve(), "libtmux")
    assert hint is not None
    assert "--server tmux" in hint


def test_naming_hint_none_when_derived_name_matches(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """No hint when the repo is already registered under the derived name."""
    _write_json(
        mcp_swap.CLIS["cursor"].config_path,
        {"mcpServers": {"libtmux": _local_entry(fake_repo)}},
    )
    assert mcp_swap._naming_hint(fake_repo.resolve(), "libtmux") is None


def test_naming_hint_none_when_repo_also_registered_under_derived(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """No hint when the derived name points here, even if another name does too.

    Regression: the hint used to fire on the other name and falsely claim
    'nothing is registered under <derived>' when the derived name in fact
    points at the repo.
    """
    _write_json(
        mcp_swap.CLIS["cursor"].config_path,
        {
            "mcpServers": {
                "libtmux": _local_entry(fake_repo),
                "tmux": _local_entry(fake_repo),
            }
        },
    )
    assert mcp_swap._naming_hint(fake_repo.resolve(), "libtmux") is None


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_reports_name_mismatch_and_auth_env(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor surfaces the server-name mismatch and auth-overriding env vars."""
    _write_json(
        mcp_swap.CLIS["cursor"].config_path,
        {"mcpServers": {"tmux": _local_entry(fake_repo)}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    args = mcp_swap.build_parser().parse_args(["doctor", "--repo", str(fake_repo)])
    assert mcp_swap.cmd_doctor(args) == 0
    out = capsys.readouterr().out
    assert "server name mismatch" in out
    assert "--server tmux" in out
    assert "OPENAI_API_KEY" in out and "codex" in out


def test_doctor_flags_missing_backup_and_orphans(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doctor flags a state entry whose backup vanished, and untracked backups."""
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _local_entry(fake_repo)}})
    # A recorded swap whose backup file does not exist -> revert would fail.
    mcp_swap.save_state(
        {
            ("cursor", "user"): mcp_swap.SwapEntry(
                config_path=str(info.config_path),
                backup_path=str(info.config_path) + ".bak.mcp-swap-20200101000000",
                server="libtmux",
                action="replaced",
                swapped_at="20200101000000",
                seq_no=0,
            )
        }
    )
    # An orphaned backup on disk not referenced by state.
    orphan = info.config_path.parent / (
        info.config_path.name + ".bak.mcp-swap-20190101000000"
    )
    orphan.write_text("stale")

    args = mcp_swap.build_parser().parse_args(["doctor", "--repo", str(fake_repo)])
    assert mcp_swap.cmd_doctor(args) == 0
    out = capsys.readouterr().out
    assert "BACKUP MISSING" in out
    assert "orphaned backups" in out


def test_orphaned_backups_matches_swap_pattern(
    fake_home: pathlib.Path,
) -> None:
    """``_orphaned_backups`` finds swap backups and ignores the live config."""
    info = mcp_swap.CLIS["cursor"]
    info.config_path.parent.mkdir(parents=True, exist_ok=True)
    info.config_path.write_text("{}\n")
    b1 = info.config_path.parent / (
        info.config_path.name + ".bak.mcp-swap-20260101000000"
    )
    b1.write_text("x")
    found = mcp_swap._orphaned_backups(info.config_path)
    assert b1 in found
    assert info.config_path not in found


def test_doctor_does_not_call_orphaned_backups_safe_to_delete(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Orphan advice must not read as "safe to delete".

    An untracked backup can be the *only* pre-swap copy of a config — a
    swap whose write failed leaves exactly that. Telling the user to bin
    it turns a recoverable state into data loss.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _local_entry(fake_repo)}})
    orphan = info.config_path.parent / (
        info.config_path.name + ".bak.mcp-swap-20190101000000"
    )
    orphan.write_text("pristine")

    args = mcp_swap.build_parser().parse_args(["doctor", "--repo", str(fake_repo)])
    assert mcp_swap.cmd_doctor(args) == 0
    out = capsys.readouterr().out
    assert "orphaned backups" in out
    assert "safe to delete" not in out
    assert "inspect before deleting" in out


# ---------------------------------------------------------------------------
# Repeat swaps must not destroy the pre-swap config.
# ---------------------------------------------------------------------------


def _freeze_timestamps(monkeypatch: pytest.MonkeyPatch, stamps: list[str]) -> None:
    """Make the script's ``time.strftime`` yield ``stamps`` in order.

    The backup filename embeds ``%Y%m%d%H%M%S``, so same-second vs
    different-second is the difference between two swaps deriving the
    same path or two distinct ones. Pinning the sequence makes both
    cases deterministic instead of a race against the wall clock. Only
    the script's own ``time`` reference is swapped, so pytest's log
    formatting keeps the real clock.
    """
    remaining = list(stamps)

    def fake_strftime(*_args: object) -> str:
        return remaining.pop(0) if remaining else stamps[-1]

    monkeypatch.setattr(mcp_swap, "time", types.SimpleNamespace(strftime=fake_strftime))


@pytest.mark.parametrize(
    ("case", "stamps"),
    [
        ("same_second", ["20260101000000", "20260101000000"]),
        ("different_second", ["20260101000000", "20260101000001"]),
    ],
)
def test_repeat_swap_then_revert_restores_pristine_config(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    stamps: list[str],
) -> None:
    """Swap -> swap -> revert must yield the byte-identical pre-swap config.

    Regression for two ways the second swap used to destroy the only
    pristine copy: with a same-second timestamp both swaps derived the
    same backup path and the second write clobbered the first; with a
    different-second timestamp the second swap wrote a fresh backup (of
    the already-swapped config) and repointed state at it, orphaning the
    pristine one. Either way ``revert`` restored a swapped config.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    original = info.config_path.read_bytes()
    _freeze_timestamps(monkeypatch, stamps)
    parser = mcp_swap.build_parser()

    def swap(value: str) -> None:
        # Distinct --env per swap so the second run is a real rewrite,
        # not the "already local" no-op.
        assert (
            mcp_swap.cmd_use_local(
                parser.parse_args(
                    [
                        "use-local",
                        "--repo",
                        str(fake_repo),
                        "--cli",
                        "cursor",
                        "--env",
                        f"LIBTMUX_SOCKET={value}",
                    ]
                )
            )
            == 0
        )

    swap("one")
    first_backup = pathlib.Path(mcp_swap.load_state()[("cursor", "user")].backup_path)
    assert first_backup.read_bytes() == original

    swap("two")

    # The second swap keeps the first backup: same path, same bytes, and
    # no second backup file left behind.
    entry = mcp_swap.load_state()[("cursor", "user")]
    assert pathlib.Path(entry.backup_path) == first_backup
    assert first_backup.read_bytes() == original
    assert mcp_swap._orphaned_backups(info.config_path) == [first_backup]

    assert mcp_swap.cmd_revert(parser.parse_args(["revert", "--cli", "cursor"])) == 0
    assert info.config_path.read_bytes() == original


def test_repeat_swap_keeps_claude_lifo_order_across_scopes(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-swapping one Claude scope must not reorder the LIFO unwind.

    Both scopes back the same physical file, so a backup's position in
    the stack is fixed by what it captured, not by when it was last
    touched. Bumping ``seq_no`` on the re-swap would make ``revert``
    peel the user layer off first and leave the project layer's backup
    (which still contains the user swap) as the final state.
    """
    info = mcp_swap.CLIS["claude"]
    _write_json(
        info.config_path,
        {
            "mcpServers": {"libtmux": _pinned_claude_entry()},
            "projects": {
                str(fake_repo.resolve()): {
                    "mcpServers": {"libtmux": _pinned_claude_entry()},
                },
            },
        },
    )
    original = info.config_path.read_bytes()
    _freeze_timestamps(
        monkeypatch, ["20260101000000", "20260101000001", "20260101000002"]
    )
    parser = mcp_swap.build_parser()

    def swap(scope: str, value: str) -> None:
        assert (
            mcp_swap.cmd_use_local(
                parser.parse_args(
                    [
                        "use-local",
                        "--repo",
                        str(fake_repo),
                        "--cli",
                        "claude",
                        "--scope",
                        scope,
                        "--env",
                        f"LIBTMUX_SOCKET={value}",
                    ]
                )
            )
            == 0
        )

    swap("user", "one")
    swap("project", "one")
    # Re-swap the *older* layer; its backup still holds the pristine file.
    swap("user", "two")

    state = mcp_swap.load_state()
    assert state[("claude", "user")].seq_no < state[("claude", "project")].seq_no

    assert mcp_swap.cmd_revert(parser.parse_args(["revert", "--cli", "claude"])) == 0
    assert info.config_path.read_bytes() == original
    assert not mcp_swap.STATE_FILE.exists()


def test_write_new_backup_never_overwrites(tmp_path: pathlib.Path) -> None:
    """A taken backup path is left alone; the write lands on a suffixed sibling."""
    base = tmp_path / "config.toml.bak.mcp-swap-20260101000000"
    first = mcp_swap.write_new_backup(base, b"pristine\n")
    assert first == base

    second = mcp_swap.write_new_backup(base, b"later\n")
    assert second == base.with_name(base.name + "-1")
    assert base.read_bytes() == b"pristine\n"
    assert second.read_bytes() == b"later\n"

    third = mcp_swap.write_new_backup(base, b"later still\n")
    assert third == base.with_name(base.name + "-2")


def test_swap_after_backup_vanished_warns_and_writes_new_backup(
    fake_home: pathlib.Path,
    fake_repo: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A re-swap whose recorded backup was deleted says so and re-registers.

    Nothing can recover the pristine bytes at that point, so the new
    backup is of the swapped config; the warning keeps that explicit
    instead of implying ``revert`` will undo the original swap.
    """
    info = mcp_swap.CLIS["cursor"]
    _write_json(info.config_path, {"mcpServers": {"libtmux": _pinned_json_entry()}})
    parser = mcp_swap.build_parser()

    def swap(value: str) -> None:
        assert (
            mcp_swap.cmd_use_local(
                parser.parse_args(
                    [
                        "use-local",
                        "--repo",
                        str(fake_repo),
                        "--cli",
                        "cursor",
                        "--env",
                        f"LIBTMUX_SOCKET={value}",
                    ]
                )
            )
            == 0
        )

    swap("one")
    stale = pathlib.Path(mcp_swap.load_state()[("cursor", "user")].backup_path)
    swapped_bytes = info.config_path.read_bytes()
    stale.unlink()
    capsys.readouterr()

    swap("two")

    assert "recorded backup is gone" in capsys.readouterr().err
    fresh = pathlib.Path(mcp_swap.load_state()[("cursor", "user")].backup_path)
    assert fresh.read_bytes() == swapped_bytes


# ---------------------------------------------------------------------------
# opencode and pi
#
# These two exercise axes the first six never did. opencode is the first
# JSONC config, the first container key that is not ``mcpServers`` or
# ``mcp_servers``, and the first entry dialect that packs argv into one
# array; pi is the first CLI whose config is read by an extension rather
# than by the agent itself. The comment-fidelity cases are the point of
# the JSONC codec, so they are asserted on bytes, not on parsed values.
# ---------------------------------------------------------------------------


def test_fake_home_covers_every_registered_cli(fake_home: pathlib.Path) -> None:
    """``fake_home`` replaces ``CLIS`` wholesale, so it must list every CLI.

    Regression guard rather than a behavior test. ``_config_present_clis``
    iterates ``ALL_CLIS`` while indexing ``CLIS``, so a CLI added to the
    registry but not to this fixture raises ``KeyError`` from half a dozen
    unrelated doctor and naming-hint tests. Naming the invariant here turns
    that into one obvious failure.
    """
    assert set(mcp_swap.CLIS) == set(mcp_swap.ALL_CLIS)


def test_opencode_and_pi_registered() -> None:
    """Both new CLIs are first-class ``--cli`` choices with their own shapes."""
    assert "opencode" in mcp_swap.ALL_CLIS
    assert "pi" in mcp_swap.ALL_CLIS
    opencode = mcp_swap.CLIS["opencode"]
    assert opencode.fmt == "jsonc"
    assert opencode.config_path.name == "opencode.jsonc"
    assert opencode.container == ("mcp",)
    assert opencode.dialect == "opencode"
    pi = mcp_swap.CLIS["pi"]
    assert pi.fmt == "json"
    assert pi.config_path.name == "mcp.json"
    assert pi.container == ("mcpServers",)
    assert pi.dialect == "standard"
    parser = mcp_swap.build_parser()
    assert parser.parse_args(["status", "--cli", "opencode"]).cli == ["opencode"]
    assert parser.parse_args(["status", "--cli", "pi"]).cli == ["pi"]


@pytest.mark.parametrize("cli", ["opencode", "pi"])
def test_new_cli_set_get_delete_roundtrip(cli: str, fake_repo: pathlib.Path) -> None:
    """Each new CLI's four container branches agree with one another.

    Proves the name was threaded through ``get_server``, ``set_server``,
    ``delete_server`` and ``_all_server_specs`` rather than falling through
    to another CLI's container key.
    """
    config: dict[str, t.Any] = {}
    spec = mcp_swap.McpServerSpec(
        command="uv", args=["--directory", str(fake_repo), "run", "libtmux-mcp"]
    )
    assert mcp_swap.set_server(cli, config, "libtmux", spec, fake_repo) == "added"
    assert mcp_swap.CLIS[cli].container[0] in config
    got = mcp_swap.get_server(cli, config, "libtmux", fake_repo)
    assert got is not None
    assert got.is_local_uv_directory()
    assert got.local_repo_path() == fake_repo
    assert mcp_swap.set_server(cli, config, "libtmux", spec, fake_repo) == "replaced"
    assert mcp_swap._all_server_specs(cli, config, fake_repo).keys() == {"libtmux"}
    assert mcp_swap.delete_server(cli, config, "libtmux", fake_repo)
    assert mcp_swap.get_server(cli, config, "libtmux", fake_repo) is None


def test_opencode_entry_packs_argv_into_one_command_array(
    fake_repo: pathlib.Path,
) -> None:
    """The opencode dialect uses one argv array and the key ``environment``.

    A scalar ``command`` is a decode error that stops opencode starting at
    all, and an ``env`` key is dropped without a warning, so both spellings
    are pinned here rather than left to the round-trip tests.
    """
    spec = mcp_swap.McpServerSpec(
        command="uv", args=["--directory", "/repo", "run"], env={"A": "b"}
    )
    entry = spec.to_entry_dict("opencode")
    assert entry["type"] == "local"
    assert entry["command"] == ["uv", "--directory", "/repo", "run"]
    assert entry["environment"] == {"A": "b"}
    assert "args" not in entry
    assert "env" not in entry


def test_opencode_array_entry_reads_back_as_command_plus_args() -> None:
    """An array ``command`` normalizes to the portable scalar-plus-args spec.

    Regression: without the split, ``command`` becomes the ``str()`` of a
    Python list, ``is_local_uv_directory`` is False for a correct entry, and
    the "already local — no change" short-circuit never fires, so every run
    rewrites a config that needed no change.
    """
    info = mcp_swap.CLIS["opencode"]
    spec = mcp_swap._spec_from_entry(
        {
            "type": "local",
            "command": ["uv", "--directory", "/repo", "run", "libtmux-mcp"],
            "environment": {"A": "b"},
        },
        info=info,
    )
    assert spec.command == "uv"
    assert spec.args == ["--directory", "/repo", "run", "libtmux-mcp"]
    assert spec.env == {"A": "b"}
    assert spec.is_local_uv_directory()
    assert spec.local_repo_path() == pathlib.Path("/repo")


def _opencode_config(fake_home: pathlib.Path, body: str) -> t.Any:
    """Write ``body`` to the fake opencode config and return its ``CLIInfo``."""
    info = mcp_swap.CLIS["opencode"]
    info.config_path.parent.mkdir(parents=True, exist_ok=True)
    info.config_path.write_text(body)
    return info


def _swap_opencode(fake_repo: pathlib.Path) -> int:
    """Run ``use-local`` against opencode only."""
    args = mcp_swap.build_parser().parse_args(
        ["use-local", "--repo", str(fake_repo), "--cli", "opencode"]
    )
    return int(mcp_swap.cmd_use_local(args))


def test_opencode_swap_preserves_jsonc_comments(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Line comments, block comments and sibling servers survive a swap."""
    info = _opencode_config(
        fake_home,
        "{\n"
        "  // header comment\n"
        '  "$schema": "https://opencode.ai/config.json",\n'
        "  /* a block comment\n"
        "     spanning lines */\n"
        '  "model": "openrouter/x",\n'
        '  "mcp": {\n'
        '    "other": { "type": "local", "command": ["echo", "keep"] }\n'
        "  }\n"
        "}\n",
    )
    assert _swap_opencode(fake_repo) == 0
    text = info.config_path.read_text()
    assert "// header comment" in text
    assert "/* a block comment" in text
    assert "spanning lines */" in text
    doc = mcp_swap._jsonc_loads(text)
    assert doc["model"] == "openrouter/x"
    assert doc["mcp"]["other"]["command"] == ["echo", "keep"]
    assert doc["mcp"]["libtmux"]["command"][0] == "uv"


def test_opencode_comment_inside_the_replaced_entry_survives(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """A comment attached to the entry being rewritten is not collateral.

    The case a whole-entry rewrite loses and a field-level splice keeps.
    Real opencode configs carry the rationale for a pinned ``command``
    directly above it, which is exactly the text a swap would destroy.
    """
    info = _opencode_config(
        fake_home,
        "{\n"
        '  "mcp": {\n'
        '    "libtmux": {\n'
        '      "type": "local",\n'
        "      // Pinned deliberately; this rationale must outlive the swap.\n"
        '      "command": ["uvx", "libtmux-mcp==0.1.0a2"],\n'
        '      "environment": { "KEEP": "me" }\n'
        "    }\n"
        "  }\n"
        "}\n",
    )
    assert _swap_opencode(fake_repo) == 0
    text = info.config_path.read_text()
    assert "// Pinned deliberately; this rationale must outlive the swap." in text
    entry = mcp_swap._jsonc_loads(text)["mcp"]["libtmux"]
    assert entry["command"][0] == "uv"
    assert entry["environment"] == {"KEEP": "me"}


def test_opencode_swap_and_revert_round_trip_is_byte_identical(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Revert restores a commented JSONC config byte for byte."""
    body = (
        "{\n"
        "  // keep me\n"
        '  "model": "m",\n'
        '  "mcp": {\n'
        '    "libtmux": {\n'
        '      "type": "local",\n'
        '      "command": ["uvx", "libtmux-mcp==0.1.0a2"]\n'
        "    }\n"
        "  }\n"
        "}\n"
    )
    info = _opencode_config(fake_home, body)
    original = info.config_path.read_bytes()
    assert _swap_opencode(fake_repo) == 0
    assert info.config_path.read_bytes() != original
    revert = mcp_swap.build_parser().parse_args(["revert", "--cli", "opencode"])
    assert mcp_swap.cmd_revert(revert) == 0
    assert info.config_path.read_bytes() == original


def test_opencode_second_swap_reports_no_change(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """The idempotence check fires for the array command shape.

    Depends on ``_spec_from_entry`` splitting the array; without it the
    config is rewritten on every invocation.
    """
    info = _opencode_config(fake_home, '{\n  "mcp": {}\n}\n')
    assert _swap_opencode(fake_repo) == 0
    after_first = info.config_path.read_bytes()
    assert _swap_opencode(fake_repo) == 0
    assert info.config_path.read_bytes() == after_first


def test_opencode_seeds_schema_into_an_empty_config(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """Seeding an empty file writes ``$schema`` alongside the server entry."""
    info = _opencode_config(fake_home, "")
    assert _swap_opencode(fake_repo) == 0
    doc = mcp_swap._jsonc_loads(info.config_path.read_text())
    assert doc["$schema"] == mcp_swap.OPENCODE_SCHEMA_URL
    assert doc["mcp"]["libtmux"]["type"] == "local"


def test_detect_reports_the_pi_adapter_prerequisite(
    fake_home: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``detect`` says why a pi swap will not take effect on its own.

    pi ships no MCP client, so the file this script writes is read only by
    the ``pi-mcp-adapter`` extension. Reporting pi as swappable without
    that caveat would be the one thing this script must never do: claim an
    agent will run something it will not.
    """
    monkeypatch.setattr(mcp_swap, "PI_ADAPTER_DIR", fake_home / "absent")
    monkeypatch.setattr(mcp_swap.shutil, "which", lambda _binary: "/usr/bin/stub")
    info = mcp_swap.CLIS["pi"]
    info.config_path.parent.mkdir(parents=True)
    info.config_path.write_text('{"mcpServers": {}}\n')

    assert mcp_swap.cmd_detect(mcp_swap.build_parser().parse_args(["detect"])) == 0
    out = capsys.readouterr().out
    assert mcp_swap.PI_ADAPTER_HINT in out

    monkeypatch.setattr(mcp_swap, "PI_ADAPTER_DIR", fake_home)
    assert mcp_swap.cmd_detect(mcp_swap.build_parser().parse_args(["detect"])) == 0
    assert mcp_swap.PI_ADAPTER_HINT not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# JSONC writer fidelity
#
# The JSON writer reserializes the whole document, so it can only promise
# to preserve values. The JSONC writer splices text and therefore promises
# bytes: anything it did not deliberately change must come back identical,
# including the comments, the trailing comma, the indent width and the
# absence of a final newline. The string cases exist because a
# comment-stripper that is not string-aware corrupts a URL or a Windows
# path silently, which is the worst failure this codec could have.
# ---------------------------------------------------------------------------


PRESERVED_JSONC: list[tuple[str, str]] = [
    ("line_comment", '{\n  // note\n  "mcp": {}\n}\n'),
    ("block_comment", '{\n  /* note\n     more */\n  "mcp": {}\n}\n'),
    ("comment_after_last_member", '{\n  "mcp": {}\n  // tail\n}\n'),
    ("trailing_comma", '{\n  "mcp": {},\n}\n'),
    ("no_trailing_newline", '{\n  "mcp": {}\n}'),
    ("four_space_indent", '{\n    "mcp": {}\n}\n'),
    ("url_containing_double_slash", '{\n  "a": "https://x/y//z"\n}\n'),
    ("block_marker_inside_string", '{\n  "a": "/* not one */"\n}\n'),
    ("windows_path", '{\n  "a": "C:\\\\tmp\\\\x"\n}\n'),
    ("literal_backslash_u", '{\n  "a": "C:\\\\u0041"\n}\n'),
    ("emoji_and_cjk", '{\n  "a": "🙂 日本語 café"\n}\n'),
    ("empty_object", "{}\n"),
]


def _jsonc_config(tmp_path: pathlib.Path, body: str) -> tuple[t.Any, bytes]:
    """Write ``body`` verbatim and return its ``CLIInfo`` and exact bytes."""
    path = tmp_path / "opencode.jsonc"
    path.write_text(body)
    info = mcp_swap.CLIInfo(
        name="opencode",
        binary="opencode",
        config_path=path,
        fmt="jsonc",
        container=("mcp",),
        dialect="opencode",
    )
    return info, path.read_bytes()


@pytest.mark.parametrize(
    ("test_id", "body"),
    PRESERVED_JSONC,
    ids=[case[0] for case in PRESERVED_JSONC],
)
def test_untouched_jsonc_config_round_trips_byte_identical(
    test_id: str, body: str, tmp_path: pathlib.Path
) -> None:
    """Loading and rewriting an unmodified JSONC config changes no byte."""
    assert test_id
    info, raw = _jsonc_config(tmp_path, body)
    config = mcp_swap.load_config(info)
    assert mcp_swap.dump_config_bytes(info, config, original=raw) == raw


@pytest.mark.parametrize(
    ("test_id", "body"),
    PRESERVED_JSONC,
    ids=[case[0] for case in PRESERVED_JSONC],
)
def test_jsonc_values_match_stdlib_json(
    test_id: str, body: str, tmp_path: pathlib.Path
) -> None:
    r"""JSONC parsing agrees with stdlib json wherever stdlib can parse.

    Escape handling is the standard library's, not a reimplementation's.
    The rejected ``json-five`` dependency failed exactly here: it raised on
    ``"C:\\x"`` and decoded a literal ``\\u0041`` to ``"A"``.
    """
    assert test_id
    try:
        expected = json.loads(body)
    except json.JSONDecodeError:
        pytest.skip("comment or trailing comma — stdlib cannot parse it")
    assert mcp_swap._jsonc_loads(body) == expected


def test_jsonc_config_is_not_written_through_the_toml_writer(
    tmp_path: pathlib.Path,
) -> None:
    """A jsonc config comes back as JSON text, not TOML.

    Regression: ``dump_config_bytes`` branched on ``fmt != "json"``, so any
    third format reached ``tomlkit.dumps`` and put TOML bytes in a JSON
    file. The dispatch is on the exact format now.
    """
    info, raw = _jsonc_config(tmp_path, '{\n  "mcp": {}\n}\n')
    out = mcp_swap.dump_config_bytes(
        info, {"mcp": {"x": {"type": "local"}}}, original=raw
    )
    text = out.decode()
    assert text.lstrip().startswith("{")
    assert mcp_swap._jsonc_loads(text)["mcp"]["x"]["type"] == "local"


def test_jsonc_comment_blanking_preserves_offsets() -> None:
    """Blanking a comment must not move the bytes around it.

    Offsets are what let a span found in the blanked text address the same
    bytes in the original; if blanking changed the length, every splice
    would land in the wrong place.
    """
    src = '{\n  // note\n  "a": 1, /* x */\n  "b": "//not a comment"\n}\n'
    blanked = mcp_swap._jsonc_blank_comments(src)
    assert len(blanked) == len(src)
    assert "//not a comment" in blanked
    assert "note" not in blanked
    assert blanked.count("\n") == src.count("\n")
