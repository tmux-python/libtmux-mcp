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
            ),
            "codex": mcp_swap.CLIInfo(
                name="codex",
                binary="codex",
                config_path=tmp_path / ".codex" / "config.toml",
                fmt="toml",
            ),
            "cursor": mcp_swap.CLIInfo(
                name="cursor",
                binary="cursor-agent",
                config_path=tmp_path / ".cursor" / "mcp.json",
                fmt="json",
            ),
            "gemini": mcp_swap.CLIInfo(
                name="gemini",
                binary="gemini",
                config_path=tmp_path / ".gemini" / "settings.json",
                fmt="json",
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
    """``libtmux-mcp`` resolves to server name ``libtmux`` and entry ``libtmux-mcp``."""
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
# JSON round-trip: cursor / gemini
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cli", ["cursor", "gemini"])
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
    table = config["mcp_servers"]["libtmux"]  # type: ignore[index]
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


def test_legacy_v1_state_migrates_to_v2_keys(
    fake_home: pathlib.Path, fake_repo: pathlib.Path
) -> None:
    """A v1 state file with bare ``cli`` keys is migrated on load.

    ``claude`` (legacy default was per-project) → ``("claude", "project")``.
    Bare ``codex`` / ``cursor`` / ``gemini`` (no per-project layer) →
    ``("<cli>", "user")``.
    """
    mcp_swap.STATE_DIR.mkdir(parents=True, exist_ok=True)
    legacy = {
        "version": 1,
        "entries": {
            "claude": {
                "config_path": "/tmp/.claude.json",
                "backup_path": "/tmp/.claude.json.bak",
                "server": "libtmux",
                "action": "replaced",
            },
            "codex": {
                "config_path": "/tmp/codex.toml",
                "backup_path": "/tmp/codex.toml.bak",
                "server": "libtmux",
                "action": "added",
            },
        },
    }
    mcp_swap.STATE_FILE.write_text(json.dumps(legacy))

    state = mcp_swap.load_state()
    assert set(state.keys()) == {("claude", "project"), ("codex", "user")}
    assert state[("claude", "project")].backup_path == "/tmp/.claude.json.bak"
    assert state[("codex", "user")].action == "added"


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
