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


# Every prehydrate declaration is ``!important``. The whole block lives in
# ``@layer mcp-install-prehydrate`` (see :func:`_build_style`) and per CSS
# Cascade Level 5 only ``!important`` declarations get the layer-priority
# *reversal* that makes a layered rule outrank an unlayered one. Normal
# (non-``!important``) rules in a layer LOSE to unlayered rules of the same
# specificity — which is what bit the original tab rules: they were
# specific enough to beat ``widget.css``'s ``.tab[aria-selected="true"]``
# unlayered, but became powerless once we wrapped the prehydrate in a layer
# to fix the panel cascade against ``furo-tw``'s ``[hidden]`` preflight.
_TAB_DEACTIVATE_RULE = (
    "html[data-mcp-install-client] .lm-mcp-install__tab"
    '[data-tab-kind="client"][aria-selected="true"],'
    "html[data-mcp-install-method] .lm-mcp-install__tab"
    '[data-tab-kind="method"][aria-selected="true"]'
    "{color:var(--color-foreground-muted) !important;"
    "border-bottom-color:transparent !important;"
    "background:transparent !important}"
)

_TAB_ACTIVE_DECL = (
    "{color:var(--color-brand-primary) !important;"
    "border-bottom-color:var(--color-brand-primary) !important;"
    "background:var(--color-background-primary) !important}"
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

    Rules are wrapped in ``@layer mcp-install-prehydrate``. ``gp-furo-theme``
    ships Tailwind v4's preflight inside ``@layer base``, including
    ``[hidden]:where(:not([hidden="until-found"])){display:none!important}``.
    Per CSS Cascade Level 5, important-rule layer ordering is reversed:
    rules in *any* cascade layer outrank ``!important`` unlayered rules
    regardless of specificity. An unlayered prehydrate ``<style>`` therefore
    loses to the preflight on the saved panel, so the saved panel paints as
    ``display:none`` until ``widget.js`` mutates ``[hidden]`` and the
    install widget visibly grows. Declaring our rules in their own layer
    makes them the *first* layer the browser encounters (the prehydrate
    ``<style>`` lives in ``metatags``, before any ``<link>``), which is
    the highest-priority layer for ``!important``.

    The reversal only applies to ``!important`` declarations. *Normal*
    layered rules LOSE to *normal* unlayered rules — so every declaration
    here is ``!important``, including the tab active/inactive colours
    that competed (and won, unlayered) against ``widget.css``'s
    ``.lm-mcp-install__tab[aria-selected="true"]`` purely on specificity.
    Drop the ``!important`` on a tab declaration and the active-tab
    indicator will flash from server default to saved state on first paint.
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
    return "<style>@layer mcp-install-prehydrate{" + "".join(rules) + "}</style>"


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
