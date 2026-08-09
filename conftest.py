"""Conftest.py (root-level).

We keep this in root pytest fixtures in pytest's doctest plugin to be available, as well
as avoiding conftest.py from being included in the wheel, in addition to pytest_plugin
for pytester only being available via the root directory.

See "pytest_plugins in non-top-level conftest files" in
https://docs.pytest.org/en/stable/deprecations.html
"""

from __future__ import annotations

import os
import shutil
import typing as t

import pytest
from _pytest.doctest import DoctestItem
from libtmux.pane import Pane
from libtmux.pytest_plugin import USING_ZSH
from libtmux.server import Server
from libtmux.session import Session
from libtmux.window import Window

if t.TYPE_CHECKING:
    import pathlib

# Run the suite with fastmcp's camelCase compatibility bridge switched
# off. fastmcp 4 still answers SDK v1 spellings like `tool.inputSchema`
# through warn-once shims that are scheduled for removal, and neither
# gate catches a read that depends on them: mypy sees `Any` (the SDK
# objects arrive through `Client.__aenter__`, which is unannotated) and
# the warning fires once per (class, attribute) for the whole run, so
# one stray read is easy to miss. With the bridge off a surviving read
# raises `AttributeError` and fails the test that made it.
#
# Set before anything imports fastmcp — its settings singleton is built
# at import — and via the environment rather than the settings object so
# the subprocess-based tests inherit it through `os.environ.copy()`.
# `test_camelcase_bridge_is_disabled_for_the_suite` asserts it took.
os.environ.setdefault("FASTMCP_MCP_CAMELCASE_COMPAT", "false")

pytest_plugins = ["pytester", "sphinx.testing.fixtures"]


@pytest.fixture(autouse=True)
def add_doctest_fixtures(
    request: pytest.FixtureRequest,
    doctest_namespace: dict[str, t.Any],
) -> None:
    """Configure doctest fixtures for pytest-doctest."""
    if isinstance(request._pyfuncitem, DoctestItem) and shutil.which("tmux"):
        request.getfixturevalue("set_home")
        doctest_namespace["Server"] = Server
        doctest_namespace["Session"] = Session
        doctest_namespace["Window"] = Window
        doctest_namespace["Pane"] = Pane
        doctest_namespace["server"] = request.getfixturevalue("server")
        doctest_namespace["Server"] = request.getfixturevalue("TestServer")
        session: Session = request.getfixturevalue("session")
        doctest_namespace["session"] = session
        doctest_namespace["window"] = session.active_window
        doctest_namespace["pane"] = session.active_pane
        doctest_namespace["request"] = request


@pytest.fixture(autouse=True)
def set_home(
    monkeypatch: pytest.MonkeyPatch,
    user_path: pathlib.Path,
) -> None:
    """Configure home directory for pytest tests."""
    monkeypatch.setenv("HOME", str(user_path))


@pytest.fixture(autouse=True)
def setup_fn(
    clear_env: None,
) -> None:
    """Function-level test configuration fixtures for pytest."""


@pytest.fixture(autouse=True, scope="session")
def setup_session(
    request: pytest.FixtureRequest,
    config_file: pathlib.Path,
) -> None:
    """Session-level test configuration for pytest."""
    if USING_ZSH:
        request.getfixturevalue("zshrc")
