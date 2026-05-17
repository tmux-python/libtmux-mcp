"""Prevent flash-of-wrong-selection on the ``mcp-install`` widget.

The widget's server-rendered HTML always marks the first
client/method/scope tab ``aria-selected="true"`` and ``hidden=""`` on
every panel except the ``(claude-code, uvx, local, off)`` cell.
``widget.js`` then reads ``localStorage`` and mutates the DOM to the
user's saved selection — a visible flash on initial page paint and on
every gp-sphinx SPA navigation between docs pages.

This module emits an inline ``<head>`` script that copies the saved
selection from ``localStorage`` onto ``<html>`` as
``data-mcp-install-client`` / ``data-mcp-install-method`` /
``data-mcp-install-scope`` / ``data-mcp-install-cooldown-enabled`` /
``data-mcp-install-cooldown-type`` / ``data-mcp-install-cooldown-days``
attributes *before first paint*, plus a ``<style>`` block whose
attribute-selector rules drive the active tab + visible scope group +
visible panel from those attributes. ``<html>`` is never replaced by
gp-sphinx's ``spa-nav.js`` (it only swaps ``.article-container``), so
the attributes survive SPA navigation and the new article paints in
the saved state without the head script needing to re-run.

Scope is **per-client**: the localStorage key is
``libtmux-mcp.mcp-install.scope.<client_id>``.

Cooldown state is **three orthogonal axes**:

* ``libtmux-mcp.mcp-install.cooldown.enabled`` — ``"1"`` / ``"0"`` —
  master on/off switch.
* ``libtmux-mcp.mcp-install.cooldown.type`` — ``"days"`` / ``"bypass"`` —
  cooldown flavor when enabled.
* ``libtmux-mcp.mcp-install.cooldown.days`` — int — day count when
  ``type=days``.

The ``cooldown-days`` attribute is read by ``widget.js`` (not by these
CSS rules) to populate the per-snippet
``[data-cooldown-duration-slot]`` / ``[data-cooldown-date-slot]``
spans. The CSS panel rule keys on the ``enabled`` + ``type`` pair to
pick which panel variant is visible.
"""

from __future__ import annotations

import json
import typing as t

from .mcp_install import (
    CLIENTS,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_COOLDOWN_ENABLED,
    DEFAULT_COOLDOWN_TYPE,
    DEFAULT_SCOPES,
    METHODS,
)

if t.TYPE_CHECKING:
    from sphinx.application import Sphinx


# Every prehydrate declaration is ``!important``. The whole block lives in
# ``@layer mcp-install-prehydrate`` (see :func:`_build_style`) and per CSS
# Cascade Level 5 only ``!important`` declarations get the layer-priority
# *reversal* that makes a layered rule outrank an unlayered one. Normal
# (non-``!important``) rules in a layer LOSE to unlayered rules of the
# same specificity — which is what bit the original tab rules: they were
# specific enough to beat ``widget.css``'s ``.tab[aria-selected="true"]``
# unlayered, but became powerless once we wrapped the prehydrate in a
# layer to fix the panel cascade against ``furo-tw``'s ``[hidden]``
# preflight.
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
    :data:`mcp_install.DEFAULT_SCOPES`, plus the cooldown defaults
    (``enabled`` / ``type`` / ``days``). Adding a client / scope /
    cooldown mode in Python auto-extends the script.
    """
    defaults_literal = json.dumps(dict(DEFAULT_SCOPES), separators=(",", ":"))
    enabled_default = "1" if DEFAULT_COOLDOWN_ENABLED else "0"
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
        'var ce=localStorage.getItem("libtmux-mcp.mcp-install.cooldown.enabled")||"'
        + enabled_default
        + '";'
        'var ct=localStorage.getItem("libtmux-mcp.mcp-install.cooldown.type")||"'
        + DEFAULT_COOLDOWN_TYPE
        + '";'
        'var cd=localStorage.getItem("libtmux-mcp.mcp-install.cooldown.days")||"'
        + str(DEFAULT_COOLDOWN_DAYS)
        + '";'
        'if(c)h.setAttribute("data-mcp-install-client",c);'
        'if(m)h.setAttribute("data-mcp-install-method",m);'
        'if(s)h.setAttribute("data-mcp-install-scope",s);'
        'h.setAttribute("data-mcp-install-cooldown-enabled",ce);'
        'h.setAttribute("data-mcp-install-cooldown-type",ct);'
        'h.setAttribute("data-mcp-install-cooldown-days",cd);'
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
    """One rule per client that has a scope group rendered.

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
    """One selector per legal (client, method, scope, cooldown-state).

    Cooldown state is the (enabled, type) pair. There are three cases:

    * ``enabled=0`` → show the panel whose ``data-cooldown="off"``,
      regardless of saved type.
    * ``enabled=1`` + ``type=days`` → show the panel whose
      ``data-cooldown="days"``.
    * ``enabled=1`` + ``type=bypass`` → show the panel whose
      ``data-cooldown="bypass"``.

    Enumerates 30 selectors per case = 90 total (same count as the
    previous single-``mode`` model, just keyed on the new attr pair).
    """
    selectors: list[str] = []
    for c in CLIENTS:
        for m in METHODS:
            for s in c.scopes:
                base = (
                    f'[data-mcp-install-client="{c.id}"]'
                    f'[data-mcp-install-method="{m.id}"]'
                    f'[data-mcp-install-scope="{s.id}"]'
                )
                panel_base = (
                    f" .lm-mcp-install__panel"
                    f'[data-client="{c.id}"]'
                    f'[data-method="{m.id}"]'
                    f'[data-scope="{s.id}"]'
                )
                # enabled=0 → off panel (type is don't-care)
                selectors.append(
                    f'html[data-mcp-install-cooldown-enabled="0"]'
                    f"{base}{panel_base}"
                    f'[data-cooldown="off"]'
                )
                # enabled=1, type=days → days panel
                selectors.append(
                    f'html[data-mcp-install-cooldown-enabled="1"]'
                    f'[data-mcp-install-cooldown-type="days"]'
                    f"{base}{panel_base}"
                    f'[data-cooldown="days"]'
                )
                # enabled=1, type=bypass → bypass panel
                selectors.append(
                    f'html[data-mcp-install-cooldown-enabled="1"]'
                    f'[data-mcp-install-cooldown-type="bypass"]'
                    f"{base}{panel_base}"
                    f'[data-cooldown="bypass"]'
                )
    return ",".join(selectors)


def _build_style() -> str:
    """Return the ``<style>`` block that drives active state from html attrs.

    Selectors are enumerated from :data:`CLIENTS` / :data:`METHODS` so
    adding a client, method, or scope auto-extends the prehydrate rules
    — no second source of truth to drift from.

    Rules are wrapped in ``@layer mcp-install-prehydrate``.
    ``gp-furo-theme`` ships Tailwind v4's preflight inside
    ``@layer base``, including
    ``[hidden]:where(:not([hidden="until-found"])){display:none!important}``.
    Per CSS Cascade Level 5, important-rule layer ordering is reversed:
    rules in *any* cascade layer outrank ``!important`` unlayered rules
    regardless of specificity. An unlayered prehydrate ``<style>``
    therefore loses to the preflight on the saved panel, so the saved
    panel paints as ``display:none`` until ``widget.js`` mutates
    ``[hidden]`` and the install widget visibly grows. Declaring our
    rules in their own layer makes them the *first* layer the browser
    encounters (the prehydrate ``<style>`` lives in ``metatags``,
    before any ``<link>``), which is the highest-priority layer for
    ``!important``.

    The reversal only applies to ``!important`` declarations. *Normal*
    layered rules LOSE to *normal* unlayered rules — so every
    declaration here is ``!important``, including the tab
    active/inactive colours that competed (and won, unlayered) against
    ``widget.css``'s ``.lm-mcp-install__tab[aria-selected="true"]``
    purely on specificity. Drop the ``!important`` on a tab
    declaration and the active-tab indicator will flash from server
    default to saved state on first paint.

    Cooldown adds a fourth dimension keyed on the (enabled, type)
    pair on ``<html>`` rather than a single ``cooldown-mode`` attr.
    The ``cooldown-days`` html attribute does NOT drive a CSS rule —
    the days number lives in spans ``widget.js`` updates by
    textContent on every change (both duration ``P<N>D`` and
    absolute-date forms coexist; uvx + pip use duration, pipx uses
    absolute).
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

    Appended to ``context["metatags"]`` so it lands in Furo's
    ``metatags`` slot (rendered before stylesheets and the ``<body>``
    open). The pair is small (~3 KB with the cooldown rules) and a
    no-op when no widget is present, so we don't bother scoping to
    pages that use the directive.
    """
    context["metatags"] = context.get("metatags", "") + _snippet()
