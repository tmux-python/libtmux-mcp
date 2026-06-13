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
