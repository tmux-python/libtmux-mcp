"""Prevent flash-of-wrong-selection on the ``mcp-install`` widget.

The widget's server-rendered HTML always marks the first client/method/scope
tab ``aria-selected="true"`` and ``hidden=""`` on every panel except the
``(claude-code, uvx, local)`` cell. ``widget.js`` then reads ``localStorage``
and mutates the DOM to the user's saved selection — a visible flash on
initial page paint and on every gp-sphinx SPA navigation between docs pages.

This module emits an inline ``<head>`` script that copies the saved selection
from ``localStorage`` onto ``<html>`` as ``data-mcp-install-client`` /
``data-mcp-install-method`` / ``data-mcp-install-scope`` attributes *before
first paint*, plus a ``<style>`` block whose attribute-selector rules drive
the active tab + visible scope group + visible panel from those attributes.
``<html>`` is never replaced by gp-sphinx's ``spa-nav.js`` (it only swaps
``.article-container``), so the attributes survive SPA navigation and the
new article paints in the saved state without the head script needing to
re-run.

Scope is **per-client**: the localStorage key is
``libtmux-mcp.mcp-install.scope.<client_id>``. Switching clients reads
that client's saved scope (falling back to ``DEFAULT_SCOPES``) and updates
``data-mcp-install-scope``. The ``DEFAULT_SCOPES`` map is serialized into
the inline script from :data:`mcp_install.DEFAULT_SCOPES` so Python stays
the single source of truth for which scope wins on first paint.
"""

from __future__ import annotations

import json
import typing as t

from .mcp_install import CLIENTS, DEFAULT_SCOPES, METHODS

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
    '[data-tab-kind="method"][aria-selected="true"],'
    "html[data-mcp-install-scope] .lm-mcp-install__tab"
    '[data-tab-kind="scope"][aria-selected="true"]'
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

_SCOPE_GROUP_ACTIVE_DECL = "{display:flex !important}"


def _script() -> str:
    """Inline ``<head>`` script that mirrors localStorage onto ``<html>``.

    Emits a ``DEFAULT_SCOPES`` object literal derived from
    :data:`mcp_install.DEFAULT_SCOPES` so adding a client/scope in
    Python auto-extends the script. The script is intentionally tiny
    (~300 bytes) and wrapped in try/catch in case ``localStorage`` is
    disabled (private browsing, restricted environments).
    """
    defaults_literal = json.dumps(dict(DEFAULT_SCOPES), separators=(",", ":"))
    return (
        '<script data-cfasync="false">(function(){'
        "try{"
        "var h=document.documentElement;"
        f"var d={defaults_literal};"
        'var c=localStorage.getItem("libtmux-mcp.mcp-install.client")||"'
        + CLIENTS[0].id
        + '";'
        'var m=localStorage.getItem("libtmux-mcp.mcp-install.method");'
        'var s=localStorage.getItem("libtmux-mcp.mcp-install.scope."+c)||d[c];'
        'if(c)h.setAttribute("data-mcp-install-client",c);'
        'if(m)h.setAttribute("data-mcp-install-method",m);'
        'if(s)h.setAttribute("data-mcp-install-scope",s);'
        "}catch(_){}"
        "})();</script>"
    )


def _tab_active_selectors(kind: str, ids: tuple[str, ...]) -> str:
    return ",".join(
        f'html[data-mcp-install-{kind}="{id_}"] .lm-mcp-install__tab'
        f'[data-tab-kind="{kind}"][data-tab-value="{id_}"]'
        for id_ in ids
    )


def _scope_tab_active_selectors() -> str:
    """Generate one selector per legal (client, scope) pair.

    Scope tabs are scoped to a client (``data-tab-client``) so the rule
    only matches the visible group. The selector key is the joint pair
    of ``data-mcp-install-client`` + ``data-mcp-install-scope`` on
    ``<html>`` — both must match for a scope tab to light up.
    """
    return ",".join(
        f'html[data-mcp-install-client="{c.id}"]'
        f'[data-mcp-install-scope="{s.id}"]'
        f' .lm-mcp-install__tab[data-tab-kind="scope"]'
        f'[data-tab-client="{c.id}"][data-tab-value="{s.id}"]'
        for c in CLIENTS
        for s in c.scopes
    )


def _scope_group_visible_selectors() -> str:
    """Generate one rule per client that has a scope group rendered.

    Single-scope clients (``len(scopes) == 1``) get no group in the
    template, so they get no rule here either.
    """
    return ",".join(
        f'html[data-mcp-install-client="{c.id}"]'
        f' .lm-mcp-install__scopes-group[data-scope-client="{c.id}"]'
        for c in CLIENTS
        if len(c.scopes) > 1
    )


def _panel_active_selectors() -> str:
    """One selector per legal (client, method, scope) triple."""
    return ",".join(
        f'html[data-mcp-install-client="{c.id}"]'
        f'[data-mcp-install-method="{m.id}"]'
        f'[data-mcp-install-scope="{s.id}"]'
        f" .lm-mcp-install__panel"
        f'[data-client="{c.id}"]'
        f'[data-method="{m.id}"]'
        f'[data-scope="{s.id}"]'
        for c in CLIENTS
        for m in METHODS
        for s in c.scopes
    )


def _build_style() -> str:
    """Return the ``<style>`` block that drives active state from html attrs.

    Selectors are enumerated from :data:`CLIENTS` / :data:`METHODS` so adding
    a client, method, or scope auto-extends the prehydrate rules — no second
    source of truth to drift from.

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

    Scope adds three new selector families: scope group visibility (one
    rule per client with >1 scope), scope tab active state (one rule per
    legal client+scope pair), and a triple-attribute panel rule that
    replaces the old client+method pair.
    """
    client_ids = tuple(c.id for c in CLIENTS)
    method_ids = tuple(m.id for m in METHODS)
    rules = [
        _TAB_DEACTIVATE_RULE,
        _tab_active_selectors("client", client_ids) + _TAB_ACTIVE_DECL,
        _tab_active_selectors("method", method_ids) + _TAB_ACTIVE_DECL,
        _scope_tab_active_selectors() + _TAB_ACTIVE_DECL,
        _scope_group_visible_selectors() + _SCOPE_GROUP_ACTIVE_DECL,
        _PANEL_HIDE_RULE,
        _panel_active_selectors() + _PANEL_ACTIVE_DECL,
    ]
    return "<style>@layer mcp-install-prehydrate{" + "".join(rules) + "}</style>"


def _snippet() -> str:
    return _build_style() + _script()


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
    (~1.5 KB) and a no-op when no widget is present, so we don't bother
    scoping to pages that use the directive.
    """
    context["metatags"] = context.get("metatags", "") + _snippet()
