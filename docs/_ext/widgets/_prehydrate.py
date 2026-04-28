"""Prevent flash-of-wrong-selection on the ``mcp-install`` widget.

The widget's server-rendered HTML always marks the first client/method tab
``aria-selected="true"`` and ``hidden=""`` on every panel except the
``(claude-code, uvx)`` cell. ``widget.js`` then reads ``localStorage`` and
mutates the DOM to the user's saved selection — a visible flash on initial
page paint and on every gp-sphinx SPA navigation between docs pages.

This module emits an inline ``<head>`` script that copies the saved selection
from ``localStorage`` onto ``<html>`` as ``data-mcp-install-client`` /
``data-mcp-install-method`` attributes *before first paint*, plus a ``<style>``
block whose attribute-selector rules drive the active tab + visible panel
from those attributes. ``<html>`` is never replaced by gp-sphinx's
``spa-nav.js`` (it only swaps ``.article-container``), so the attributes
survive SPA navigation and the new article paints in the saved state without
the head script needing to re-run.
"""

from __future__ import annotations

import typing as t

from .mcp_install import CLIENTS, METHODS

if t.TYPE_CHECKING:
    from sphinx.application import Sphinx


_TAB_DEACTIVATE_RULE = (
    "html[data-mcp-install-client] .lm-mcp-install__tab"
    '[data-tab-kind="client"][aria-selected="true"],'
    "html[data-mcp-install-method] .lm-mcp-install__tab"
    '[data-tab-kind="method"][aria-selected="true"]'
    "{color:var(--color-foreground-muted);"
    "border-bottom-color:transparent;"
    "background:transparent}"
)

_TAB_ACTIVE_DECL = (
    "{color:var(--color-brand-primary);"
    "border-bottom-color:var(--color-brand-primary);"
    "background:var(--color-background-primary)}"
)

_PANEL_HIDE_RULE = (
    "html[data-mcp-install-client] .lm-mcp-install__panel:not([hidden])"
    "{display:none !important}"
)

_PANEL_ACTIVE_DECL = "{display:block !important}"

_SCRIPT = (
    '<script data-cfasync="false">(function(){'
    "try{"
    "var h=document.documentElement;"
    'var c=localStorage.getItem("libtmux-mcp.mcp-install.client");'
    'var m=localStorage.getItem("libtmux-mcp.mcp-install.method");'
    'if(c)h.setAttribute("data-mcp-install-client",c);'
    'if(m)h.setAttribute("data-mcp-install-method",m);'
    "}catch(_){}"
    "})();</script>"
)


def _tab_active_selectors(kind: str, ids: tuple[str, ...]) -> str:
    return ",".join(
        f'html[data-mcp-install-{kind}="{id_}"] .lm-mcp-install__tab'
        f'[data-tab-kind="{kind}"][data-tab-value="{id_}"]'
        for id_ in ids
    )


def _panel_active_selectors(
    client_ids: tuple[str, ...],
    method_ids: tuple[str, ...],
) -> str:
    return ",".join(
        f'html[data-mcp-install-client="{c}"][data-mcp-install-method="{m}"]'
        f' .lm-mcp-install__panel[data-client="{c}"][data-method="{m}"]'
        for c in client_ids
        for m in method_ids
    )


def _build_style() -> str:
    """Return the ``<style>`` block that drives active state from html attrs.

    Selectors are enumerated from :data:`CLIENTS` / :data:`METHODS` so adding
    a client or method auto-extends the prehydrate rules — no second source of
    truth to drift from.
    """
    client_ids = tuple(c.id for c in CLIENTS)
    method_ids = tuple(m.id for m in METHODS)
    rules = [
        _TAB_DEACTIVATE_RULE,
        _tab_active_selectors("client", client_ids) + _TAB_ACTIVE_DECL,
        _tab_active_selectors("method", method_ids) + _TAB_ACTIVE_DECL,
        _PANEL_HIDE_RULE,
        _panel_active_selectors(client_ids, method_ids) + _PANEL_ACTIVE_DECL,
    ]
    return "<style>" + "".join(rules) + "</style>"


def _snippet() -> str:
    return _build_style() + _SCRIPT


def inject_mcp_install_prehydrate(
    app: Sphinx,
    pagename: str,
    templatename: str,
    context: dict[str, t.Any],
    doctree: object,
) -> None:
    """Inject the prehydrate ``<style>`` + ``<script>`` into Furo's ``<head>``.

    Appended to ``context["metatags"]`` so it lands in Furo's ``metatags`` slot
    (rendered before stylesheets and the ``<body>`` open). The pair is small
    (~1 KB) and a no-op when no widget is present, so we don't bother scoping
    to pages that use the directive.
    """
    context["metatags"] = context.get("metatags", "") + _snippet()
