"""Tests for the server cache and its liveness probe."""

from __future__ import annotations

import typing as t

import pytest

from libtmux_mcp._servers import _get_server, _invalidate_server, _server_cache


def test_get_server_creates_server() -> None:
    """_get_server creates a Server instance."""
    server = _get_server(socket_name="test_mcp_util")
    assert server is not None
    assert server.socket_name == "test_mcp_util"


def test_get_server_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_server returns the same instance for the same socket."""
    _server_cache.clear()

    # Simulate a live server so the cache is not evicted. Patched on the
    # probe rather than on ``Server.is_alive``: _get_server reads the
    # cached handle's liveness through the BOUNDED probe now, so a
    # server it cannot reach is refused rather than silently replaced.
    from libtmux_mcp import _servers

    monkeypatch.setattr(_servers, "_probe_liveness", lambda server: (True, None))
    s1 = _get_server(socket_name="test_cache")
    s2 = _get_server(socket_name="test_cache")
    assert s1 is s2
    # Verify 3-tuple cache key includes tmux_bin
    assert (s1.socket_name, None, None) in _server_cache


def test_get_server_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_server reads LIBTMUX_SOCKET env var."""
    _server_cache.clear()
    monkeypatch.setenv("LIBTMUX_SOCKET", "env_socket")
    server = _get_server()
    assert server.socket_name == "env_socket"


def test_get_server_evicts_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_server evicts cached server when is_alive returns False."""
    _server_cache.clear()
    s1 = _get_server(socket_name="test_evict")
    # Patch is_alive to return False to simulate a dead server
    monkeypatch.setattr(s1, "is_alive", lambda: False)
    s2 = _get_server(socket_name="test_evict")
    assert s1 is not s2


def test_invalidate_server() -> None:
    """_invalidate_server removes matching entries from cache."""
    _server_cache.clear()
    _get_server(socket_name="test_inv")
    assert len(_server_cache) == 1
    _invalidate_server(socket_name="test_inv")
    assert len(_server_cache) == 0


# ---------------------------------------------------------------------------
# _tmux_argv tests
# ---------------------------------------------------------------------------


class LivenessProbeFixture(t.NamedTuple):
    """Test fixture for :func:`_probe_liveness`."""

    test_id: str
    returncode: int
    stderr: list[str]
    expected_alive: bool
    expected_unreachable: str | None


LIVENESS_PROBE_FIXTURES: list[LivenessProbeFixture] = [
    LivenessProbeFixture("running", 0, [], True, None),
    LivenessProbeFixture(
        "no_daemon", 1, ["no server running on /tmp/tmux-1000/x"], False, None
    ),
    LivenessProbeFixture("missing_socket", 1, ["error connecting to /x"], False, None),
    # A live server this tmux binary cannot speak to. Reporting it the
    # same as "no server" tells the agent the user's work is gone.
    LivenessProbeFixture(
        "protocol_mismatch",
        1,
        ["server exited unexpectedly"],
        False,
        "server exited unexpectedly",
    ),
]


@pytest.mark.parametrize(
    LivenessProbeFixture._fields,
    LIVENESS_PROBE_FIXTURES,
    ids=[fixture.test_id for fixture in LIVENESS_PROBE_FIXTURES],
)
def test_probe_liveness_separates_absent_from_unreachable(
    test_id: str,
    returncode: int,
    stderr: list[str],
    expected_alive: bool,
    expected_unreachable: str | None,
) -> None:
    """Absent and unreachable are different answers.

    ``Server.is_alive()`` collapses both to False and ``Server.sessions``
    degrades to ``[]`` for both, so an ordinary tmux upgrade -- sockets
    outlive the binary that made them -- made a live server report as
    absent with no error. Driven off a fake result rather than a second
    tmux binary so the assertion does not depend on the CI tmux version.
    """
    import subprocess

    from libtmux_mcp import _servers

    assert test_id

    result = subprocess.CompletedProcess(
        args=["tmux"], returncode=returncode, stdout="", stderr="\n".join(stderr)
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_servers, "_run_tmux_sync", lambda *a, **k: result)
        alive, unreachable = _servers._probe_liveness(t.cast("t.Any", object()))

    assert alive is expected_alive
    assert unreachable == expected_unreachable


def test_probe_liveness_reports_a_socket_that_answers_nothing() -> None:
    """Silence is a third answer, and stderr cannot carry it.

    A tmux server spinning inside its own event loop accepts the
    connection and writes nothing, so the two cases this probe exists to
    separate -- absent and unreachable -- are both wrong, and the probe
    itself used to hang on the socket it was classifying.
    """
    from libtmux_mcp import _servers

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_servers, "_run_tmux_sync", lambda *a, **k: None)
        alive, unreachable = _servers._probe_liveness(t.cast("t.Any", object()))

    assert alive is False
    assert unreachable is _servers.HUNG_SOCKET_REASON
