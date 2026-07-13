"""Tests for docs topic claims that describe runtime contracts."""

from __future__ import annotations

import pathlib
import typing as t

import pytest


class TopicContractFixture(t.NamedTuple):
    """Fixture for forbidden stale docs claims."""

    test_id: str
    relative_path: str
    forbidden_text: str


TOPIC_CONTRACT_FIXTURES: list[TopicContractFixture] = [
    TopicContractFixture(
        "completion_fastmcp_builtin",
        "topics/completion.md",
        "inherits FastMCP's built-in",
    ),
    TopicContractFixture(
        "pagination_automatic",
        "topics/pagination.md",
        "pagination automatically",
    ),
]


@pytest.mark.parametrize(
    TopicContractFixture._fields,
    TOPIC_CONTRACT_FIXTURES,
    ids=[f.test_id for f in TOPIC_CONTRACT_FIXTURES],
)
def test_topic_docs_do_not_overclaim_runtime_features(
    docs_dir: pathlib.Path,
    test_id: str,
    relative_path: str,
    forbidden_text: str,
) -> None:
    """Topic docs do not describe unsupported FastMCP runtime behavior."""
    assert test_id
    text = (docs_dir / relative_path).read_text(encoding="utf-8")

    assert forbidden_text not in text


def test_configuration_documents_command_history_default_and_restart(
    docs_dir: pathlib.Path,
) -> None:
    """The startup setting controls only omitted MCP run-command arguments."""
    text = (docs_dir / "configuration.md").read_text(encoding="utf-8")

    assert "```{envvar} LIBTMUX_SUPPRESS_HISTORY" in text
    assert "**Default:** `1` (enabled)" in text
    assert "Unset and `1` enable suppression; `0` disables it" in text
    assert "Any other value fails server startup" in text
    assert "LIBTMUX_SUPPRESS_HISTORY must be unset, '0', or '1'" in text
    assert (
        "applies only when an MCP caller omits `suppress_history` from "
        "{tooliconl}`run-command`"
    ) in text
    assert "explicit `suppress_history` value wins" in text
    assert "prefixes one space" in text
    assert "set `suppress_history=false` for intentional multiline input" in text
    assert "Direct Python calls default to `False`" in text
    assert "Restart the MCP server only after changing this startup setting" in text


def test_target_docs_publish_caller_aware_precedence(
    docs_dir: pathlib.Path,
) -> None:
    """Configuration, prompting, and recovery agree on target selection."""
    relative_paths = (
        "configuration.md",
        "topics/prompting.md",
        "topics/troubleshooting.md",
    )
    precedence = (
        "explicit per-call selector, configured path, configured name, "
        "frozen caller socket, tmux default"
    )

    for relative_path in relative_paths:
        text = (docs_dir / relative_path).read_text(encoding="utf-8")
        assert precedence in " ".join(text.split())

    configuration = (docs_dir / "configuration.md").read_text(encoding="utf-8")
    troubleshooting = (docs_dir / "topics/troubleshooting.md").read_text(
        encoding="utf-8"
    )
    normalized_configuration = " ".join(configuration.split())
    assert "inside tmux, the frozen caller socket" in normalized_configuration
    assert "outside tmux, the tmux default" in normalized_configuration
    assert "remove `LIBTMUX_SOCKET` from the config to use the default socket" not in (
        troubleshooting
    )


def test_where_am_i_tool_page_is_indexed_and_documents_typed_states(
    docs_dir: pathlib.Path,
) -> None:
    """Invocation discovery has a complete task page and resolvable entries."""
    page = (docs_dir / "tools" / "server" / "where-am-i.md").read_text(encoding="utf-8")
    server_index = (docs_dir / "tools" / "server" / "index.md").read_text(
        encoding="utf-8"
    )
    prompting = (docs_dir / "topics" / "prompting.md").read_text(encoding="utf-8")

    assert "```{fastmcp-tool} server_tools.where_am_i" in page
    assert "```{fastmcp-tool-input} server_tools.where_am_i" in page
    for section in ("**Use when**", "**Avoid when**", "**Side effects:**"):
        assert section in page
    for state in ("outside tmux", "dead", "stale", "mismatch"):
        assert state in page.lower()
    for field in (
        "inside_tmux",
        "self_available",
        "pane_id",
        "window_id",
        "session_id",
        "server_running",
    ):
        assert f'"{field}"' in page
    assert "{tooliconl}`where-am-i`" in server_index
    assert "\nwhere-am-i\n" in server_index
    assert "{toolref}`where-am-i`" in prompting


@pytest.mark.parametrize(
    "relative_path",
    ("tools/session/list-windows.md", "tools/window/list-panes.md"),
)
def test_caller_session_tool_pages_name_lookup_tradeoff(
    docs_dir: pathlib.Path,
    relative_path: str,
) -> None:
    """Caller-local discovery states the extra lookup and its benefit."""
    normalized = " ".join(
        (docs_dir / relative_path).read_text(encoding="utf-8").split()
    )

    assert "extra targeted tmux lookup" in normalized
    assert "fail-closed live-session accuracy" in normalized


def test_configuration_separates_spawn_persistent_history_control(
    docs_dir: pathlib.Path,
) -> None:
    """Spawn history controls stay explicit and independent of startup."""
    text = (docs_dir / "configuration.md").read_text(encoding="utf-8")

    assert "`suppress_persistent_history`" in text
    assert "defaults to `false` for MCP and direct Python calls" in text
    assert "never inherits this startup setting" in text
    assert "Setting it to `true` copies and merges" in text
    assert "Leaving it `false` adds no history controls" in text
    assert "cannot remove inherited, session, or startup-file controls" in text
    assert "without including the conflicting value" in text
    spawn_control = next(
        paragraph
        for paragraph in text.split("\n\n")
        if paragraph.startswith("Process creation uses a separate control")
    )
    for tool in (
        "create-session",
        "create-window",
        "split-window",
        "respawn-pane",
    ):
        assert f"{{toolref}}`{tool}`" in spawn_control
        assert f"{{tooliconl}}`{tool}`" not in spawn_control
    for tool in ("send-keys", "send-keys-batch", "paste-text", "paste-buffer"):
        assert f"{{toolref}}`{tool}`" in text


def test_history_topic_documents_shell_limits_and_raw_input_boundary(
    docs_dir: pathlib.Path,
) -> None:
    """History guidance stays best effort and corrects the Bash default claim."""
    text = (docs_dir / "topics" / "history-suppression.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "# History suppression" in text
    assert "`ignorespace` is not a Bash default" in normalized
    assert "(history-hygiene)=" in text
    assert "(history-suppression)=" in text
    assert "Startup files can still replace" in normalized
    assert "in-memory history" in normalized
    assert "HIST_IGNORE_SPACE" in text
    assert "fish_should_add_to_history" in text
    assert "bracketed paste" in normalized
    assert "recallable until the next command" in normalized
    assert "enabled by default for omitted MCP calls" in normalized
    assert "`suppress_persistent_history=true`" in text
    assert "disabled by default, so you opt in per call" in normalized
    assert "initial pane and future panes inherit" in normalized
    assert "only to the process started by" in normalized
    assert "cannot remove settings inherited" in normalized
    assert "fill an interactive shell's history with orchestration noise" in normalized
    assert "deliberately stays out of the way on raw input" in normalized
    assert "{tooliconl}`send-keys-batch`" in text
    assert "do not inherit {envvar}`LIBTMUX_SUPPRESS_HISTORY`" in normalized
    assert "control keys such as `C-c`, TUI input, or partial text" in normalized
    assert "Paste tools have no suppression argument" in normalized
    assert "github.com/tianon/mirror-bash/blob/bash-5.3" in text
    assert "the default for bash" not in text
    assert "| Workflow |" not in text
    assert "| Shell |" not in text
    spawn_scope = text.split("## Opt into stronger controls for a new shell", 1)[
        1
    ].split("## Raw input and paste stay explicit", 1)[0]
    for tool in (
        "create-session",
        "create-window",
        "split-window",
        "respawn-pane",
    ):
        assert f"{{tooliconl}}`{tool}`" in spawn_scope


@pytest.mark.parametrize(
    ("relative_path", "required_scope", "tool_slug"),
    (
        (
            "tools/server/create-session.md",
            "future panes in that session",
            "create-session",
        ),
        (
            "tools/session/create-window.md",
            "only the spawned process",
            "create-window",
        ),
        (
            "tools/window/split-window.md",
            "only the spawned process",
            "split-window",
        ),
        (
            "tools/pane/respawn-pane.md",
            "only the spawned process",
            "respawn-pane",
        ),
    ),
)
def test_spawn_tool_pages_document_history_environment_scope(
    docs_dir: pathlib.Path,
    relative_path: str,
    required_scope: str,
    tool_slug: str,
) -> None:
    """Each spawn page says how far its history environment propagates."""
    text = (docs_dir / relative_path).read_text(encoding="utf-8")

    assert "`suppress_persistent_history`" in text
    assert required_scope in text
    assert "defaults to `false` for MCP and direct Python calls" in text
    assert "does not inherit {envvar}`LIBTMUX_SUPPRESS_HISTORY`" in text
    assert "Leave it `false` to add no history controls" in text
    assert "cannot remove inherited, session, or startup-file controls" in text
    assert "in-memory history" in text
    assert "startup file can override" in text
    assert "tmux environment arguments are added" in text
    assert "spawned process command text is not prefixed or rewritten" in text
    assert f"{{tooliconl}}`{tool_slug}`" in text
    assert "`suppress_history`" not in text
    assert "follows the startup default" not in text
    assert "does not rewrite command text or tmux launch arguments" not in text


def test_create_session_page_warns_against_literal_credentials(
    docs_dir: pathlib.Path,
) -> None:
    """Create-session gives an early warning and keeps generated detail."""
    text = (docs_dir / "tools" / "server" / "create-session.md").read_text(
        encoding="utf-8"
    )
    caution = "**Do not pass credentials directly in `environment`.**"
    caution_index = text.index(caution)
    history_index = text.index("`suppress_persistent_history`")
    caution_block = text[caution_index:history_index]
    normalized = " ".join(caution_block.split())

    assert text.index("**Side effects:**") < caution_index < history_index
    assert "Values persist in the new session" in normalized
    assert "initial pane and future panes" in normalized
    assert "Pass credential references instead" in normalized
    assert "{ref}`safety`" in caution_block
    assert "```{fastmcp-tool-input} server_tools.create_session" in text


def test_run_command_page_documents_effective_history_policy(
    docs_dir: pathlib.Path,
) -> None:
    """The semantic command page distinguishes startup and explicit policy."""
    text = (docs_dir / "tools" / "pane" / "run-command.md").read_text(encoding="utf-8")

    assert "{envvar}`LIBTMUX_SUPPRESS_HISTORY`" in text
    assert "enabled by default" in text
    assert "only omitted MCP `suppress_history` arguments" in text
    assert "explicit `suppress_history` value wins" in text
    assert "Direct Python calls default to `False`" in text
    assert "`suppress_history=false` permits intentional multiline input" in text
    assert "existing shell" in text
    assert "best effort" in text
    assert (
        "generated parameter table below reflects the direct Python signature" in text
    )
    assert "`suppress_history=False`" in text
    assert "MCP `tools/list` advertises the effective suppression default" in text
    assert "`true` unless {envvar}`LIBTMUX_SUPPRESS_HISTORY` is `0`" in text


def test_safety_docs_name_history_non_goals_and_secret_reference_guidance(
    docs_dir: pathlib.Path,
) -> None:
    """Safety guidance does not present history suppression as secret transport."""
    text = (docs_dir / "topics" / "safety.md").read_text(encoding="utf-8")

    for surface in (
        "pane echo",
        "scrollback",
        "capture tools",
        "hooks",
        "process visibility",
        "MCP client transcripts",
        "logs",
    ):
        assert surface in text
    assert "credential references" in text
    assert "literal credentials" in text
    assert "`suppress_history`" in text
    assert "`suppress_persistent_history=true`" in text
    assert "does not isolate the process" in text
    assert "does not clear in-memory history or scrollback" in text
    assert "attached terminal" in text
    assert "application logs" in text
    assert "shell-joined tmux arguments" in text
    assert "does not appear verbatim in `libtmux_mcp.audit`" in text
    assert "does not contain tool return values" in text
    assert "Redaction applies only to these audit records" in text
    assert "libtmux, FastMCP, shells, or MCP clients" in text
    assert "A JSON string is redacted as one scalar digest" in text
    assert "dict-shaped sensitive key `environment`" not in text
    capture_visibility = next(
        line
        for line in text.splitlines()
        if line.startswith("- **capture tools and piping:**")
    )
    for tool in (
        "capture-pane",
        "capture-since",
        "snapshot-pane",
        "search-panes",
        "pipe-pane",
    ):
        assert f"{{toolref}}`{tool}`" in capture_visibility
        assert f"{{tooliconl}}`{tool}`" not in capture_visibility
    process_visibility = next(
        line
        for line in text.splitlines()
        if line.startswith("- **process visibility:**")
    )
    for tool in (
        "create-session",
        "create-window",
        "split-window",
        "respawn-pane",
    ):
        assert f"{{toolref}}`{tool}`" in process_visibility
    for boundary in (
        "tmux client argv",
        "child process environment",
        "tmux session state",
        "MCP audit redaction",
    ):
        assert boundary in process_visibility


def test_respawn_page_distinguishes_environment_audit_shapes(
    docs_dir: pathlib.Path,
) -> None:
    """Respawn guidance distinguishes mapping and JSON string redaction."""
    text = (docs_dir / "tools" / "pane" / "respawn-pane.md").read_text(encoding="utf-8")

    assert "Mapping input keeps the keys visible" in text
    assert "A JSON object string is redacted as one scalar digest" in text


def test_logging_docs_describe_audit_outcomes_without_return_values(
    docs_dir: pathlib.Path,
) -> None:
    """Audit guidance distinguishes call outcome from returned tool data."""
    text = (docs_dir / "topics" / "logging.md").read_text(encoding="utf-8")

    assert (
        "the ``libtmux_mcp.audit`` log shows the invocation and whether it "
        "returned or raised, not the tool's return value."
    ) in text
    assert "`suppress_history` and `suppress_persistent_history`" in text
    assert "do not disable audit logging" in text
    assert "do not clear pane echo or scrollback" in text
    assert "MCP client can still retain the original request and response" in text


def test_a17_changelog_summarizes_history_features(
    docs_dir: pathlib.Path,
) -> None:
    """The 0.1.0a17 changelog stays focused on product-level features."""
    text = (docs_dir.parent / "CHANGES").read_text(encoding="utf-8")
    release_lines = text.split("## libtmux-mcp 0.1.0a17 ", maxsplit=1)[1].splitlines()
    next_release_index = next(
        index
        for index, line in enumerate(release_lines)
        if line.startswith("## libtmux-mcp ")
    )
    release = "\n".join(release_lines[:next_release_index])

    breaking_index = release.index("### Breaking changes")
    whats_new_index = release.index("### What's new")
    history_heading = "**History controls for spawned shells**"
    environment_heading = "**Per-process environments for windows and panes**"
    breaking_entry = release[:whats_new_index]
    history_entry = release.split(history_heading, maxsplit=1)[1].split(
        environment_heading, maxsplit=1
    )[0]
    environment_entry = release.split(environment_heading, maxsplit=1)[1].split(
        "### Documentation", maxsplit=1
    )[0]

    assert breaking_index < whats_new_index
    assert "**History suppression now defaults on for run commands**" in breaking_entry
    assert "MCP clients now see the effective server default" in breaking_entry
    assert "calls that omit the argument inherit it" in breaking_entry
    assert (
        "while suppression is enabled, pass `suppress_history=false`" in breaking_entry
    )
    assert "set {envvar}`LIBTMUX_SUPPRESS_HISTORY` to `0`" in breaking_entry
    assert "{tooliconl}`run-command`" in breaking_entry
    assert "{ref}`configuration`" in breaking_entry
    assert "space-prefixed" not in breaking_entry

    for tool in (
        "create-session",
        "create-window",
        "split-window",
        "respawn-pane",
    ):
        assert f"{{tooliconl}}`{tool}`" in history_entry
    assert "`suppress_persistent_history=true`" in history_entry
    assert "Session controls reach the initial and future panes" in history_entry
    assert "{ref}`history-hygiene`" in history_entry
    assert "{ref}`safety`" in history_entry
    assert "space-prefixed" not in history_entry

    assert "{tooliconl}`create-window`" in environment_entry
    assert "{tooliconl}`split-window`" in environment_entry
    assert (
        "per-process `environment` mappings or JSON object strings" in environment_entry
    )
    assert "without changing the tmux session environment" in environment_entry
    assert "{tooliconl}`respawn-pane`" in environment_entry
    assert "same JSON object form" in environment_entry
    assert "credential references, not literal credentials" in environment_entry
    assert "{ref}`safety`" in environment_entry
