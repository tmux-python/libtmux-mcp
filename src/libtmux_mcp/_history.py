"""Semantic shell-history policy helpers."""

from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


_HISTORY_DEFAULT_TOOLS = (
    "run_command",
    "create_session",
    "create_window",
    "split_window",
    "respawn_pane",
)


def _resolve_suppress_history(value: str | None) -> bool:
    """Resolve the strict startup history-suppression setting."""
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    msg = "LIBTMUX_SUPPRESS_HISTORY must be unset, '0', or '1'"
    raise ValueError(msg)


def _configure_history_defaults(
    mcp: FastMCP,
    enabled: bool,
    *,
    tool_names: tuple[str, ...] = _HISTORY_DEFAULT_TOOLS,
) -> None:
    """Publish the effective MCP default for semantic command and spawn tools.

    Parameters
    ----------
    mcp : FastMCP
        Server receiving the public tool transform.
    enabled : bool
        Effective startup default to publish.
    tool_names : tuple[str, ...]
        Semantic tool names that inherit the default when omitted by an MCP
        caller.
    """
    from fastmcp.server.transforms import ToolTransform
    from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig

    argument = ArgTransformConfig(default=enabled)
    mcp.add_transform(
        ToolTransform(
            {
                name: ToolTransformConfig(arguments={"suppress_history": argument})
                for name in tool_names
            }
        )
    )


def _prepare_spawn_environment(
    environment: dict[str, str] | str | None,
    *,
    suppress_history: bool,
) -> dict[str, str] | None:
    """Copy and normalize an environment for a newly spawned process.

    Parameters
    ----------
    environment : dict, str, or None
        Environment mapping or JSON-object string supplied by the caller.
    suppress_history : bool
        Whether to merge the best-effort shell-history controls.

    Returns
    -------
    dict or None
        A copied environment, or ``None`` when the caller supplied no
        environment and suppression is disabled.

    Raises
    ------
    ExpectedToolError
        If keys or values are not strings, or a caller value conflicts with a
        required history control.
    """
    from libtmux_mcp._utils import ExpectedToolError, _coerce_dict_arg

    coerced = _coerce_dict_arg("environment", environment)
    if coerced is None:
        result: dict[str, str] = {}
    else:
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in coerced.items()
        ):
            msg = "environment keys and values must be strings"
            raise ExpectedToolError(msg)
        result = dict(coerced)

    if not suppress_history:
        return None if coerced is None else result

    required_values = {
        "HISTFILE": "",
        "fish_history": "",
        "fish_private_mode": "1",
    }
    corrections = {
        "HISTFILE": "omit it or set it to an empty string",
        "fish_history": "omit it or set it to an empty string",
        "fish_private_mode": "omit it or set it to '1'",
    }
    for name, required in required_values.items():
        if name in result and result[name] != required:
            msg = (
                f"environment variable {name} conflicts with "
                f"suppress_history=True; {corrections[name]}"
            )
            raise ExpectedToolError(msg)
        result[name] = required

    history_control = result.get("HISTCONTROL", "")
    tokens = [token for token in history_control.split(":") if token]
    if "ignorespace" not in tokens and "ignoreboth" not in tokens:
        tokens.append("ignorespace")
    result["HISTCONTROL"] = ":".join(tokens)
    return result
