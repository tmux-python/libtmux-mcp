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


def test_history_gotcha_documents_shell_limits_and_raw_input_boundary(
    docs_dir: pathlib.Path,
) -> None:
    """History guidance stays best effort and corrects the Bash default claim."""
    text = (docs_dir / "topics" / "gotchas.md").read_text(encoding="utf-8")

    assert "## Shell-history suppression is best effort" in text
    assert "`ignorespace` is not a Bash default" in text
    assert "(history-hygiene)=" in text
    assert "Startup files can override" in text
    assert "in-memory history" in text
    assert "HIST_IGNORE_SPACE" in text
    assert "fish_should_add_to_history" in text
    assert "bracketed paste" in text
    assert "recallable until the next command" in text
    assert "enabled by default for omitted MCP calls" in text
    assert "`suppress_persistent_history=true`" in text
    assert "defaults to `false`" in text
    assert "initial pane and future panes in that session" in text
    assert "only the process that each call starts" in text
    assert "cannot remove inherited, session, or startup-file controls" in text
    assert "{tooliconl}`send-keys-batch`" in text
    assert "do not inherit {envvar}`LIBTMUX_SUPPRESS_HISTORY`" in text
    assert "control keys such as `C-c`, TUI input, or partial text" in text
    assert "Paste tools have no suppression argument" in text
    assert "github.com/tianon/mirror-bash/blob/bash-5.3" in text
    assert "the default for bash" not in text
    spawn_scope = next(
        paragraph
        for paragraph in text.split("\n\n")
        if paragraph.startswith("For a process you are about to spawn")
    )
    for tool in (
        "create-session",
        "create-window",
        "split-window",
        "respawn-pane",
    ):
        assert f"{{toolref}}`{tool}`" in spawn_scope
        assert f"{{tooliconl}}`{tool}`" not in spawn_scope


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


def test_unreleased_changelog_documents_history_suppression(
    docs_dir: pathlib.Path,
) -> None:
    """The unreleased changelog exposes the history-control deliverable."""
    text = (docs_dir.parent / "CHANGES").read_text(encoding="utf-8")
    placeholder_end = (
        "<!-- END PLACEHOLDER - ADD NEW CHANGELOG ENTRIES BELOW THIS LINE -->"
    )
    after_placeholder = text.split(placeholder_end, maxsplit=1)[1]
    after_placeholder_lines = after_placeholder.splitlines()
    next_release_index = next(
        index
        for index, line in enumerate(after_placeholder_lines)
        if line.startswith("## libtmux-mcp ")
    )
    unreleased = "\n".join(after_placeholder_lines[:next_release_index])

    breaking_index = unreleased.index("### Breaking changes")
    whats_new_index = unreleased.index("### What's new")

    assert breaking_index < whats_new_index
    assert "**MCP command-history suppression defaults on**" in unreleased
    assert "MCP calls that omit `suppress_history`" in unreleased
    assert "pass `suppress_history=false` for that call" in unreleased
    assert "set {envvar}`LIBTMUX_SUPPRESS_HISTORY` to `0`" in unreleased
    assert "**Best-effort shell-history suppression**" in unreleased
    assert "{tooliconl}`run-command`" in unreleased
    assert "{tooliconl}`create-session`" in unreleased
    assert "{tooliconl}`create-window`" in unreleased
    assert "{tooliconl}`split-window`" in unreleased
    assert "{tooliconl}`respawn-pane`" in unreleased
    assert "{envvar}`LIBTMUX_SUPPRESS_HISTORY`" in unreleased
    assert "`suppress_persistent_history=false`" in unreleased
    assert "{toolref}`send-keys-batch`" in unreleased
    assert "{toolref}`paste-text`" in unreleased
    assert "{ref}`configuration`" in unreleased
