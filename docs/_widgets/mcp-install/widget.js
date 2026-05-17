/* MCP install widget — SPA-safe tab sync + localStorage persistence.
 *
 * Uses document-level event delegation so listeners survive gp-sphinx SPA
 * navigation (which swaps .article-container via .replaceWith()). Saved
 * localStorage state is re-applied on DOMContentLoaded and on every
 * gp-sphinx:navigated event (see sphinx-gp-theme's README for the contract).
 *
 * Visibility is fully CSS-driven by <html data-mcp-install-*> attrs and the
 * @layer mcp-install-prehydrate rules in docs/_ext/widgets/_prehydrate.py.
 * This script never mutates the panels' [hidden] attributes — it only
 * keeps tab aria-selected and the <html> data-attrs in sync with the
 * current selection. The CSS handles the rest.
 *
 * Scope is per-client: the localStorage key is
 * libtmux-mcp.mcp-install.scope.<client_id>. Switching clients reads
 * that client's saved scope (or DEFAULT_SCOPES fallback) and re-applies
 * it; switching scope writes to the current client's slot only.
 *
 * Vanilla JS, no deps.
 */
(function () {
  "use strict";

  var STORAGE = {
    client: "libtmux-mcp.mcp-install.client",
    method: "libtmux-mcp.mcp-install.method",
    scope: function (client) { return "libtmux-mcp.mcp-install.scope." + client; },
  };

  // Mirror of docs/_ext/widgets/mcp_install.py:DEFAULT_SCOPES. The prehydrate
  // <head> script emits the same map; this duplicate is small enough (5
  // entries) that the cost of keeping them in sync beats reading the literal
  // back out of the DOM. Update both when adding a client.
  var DEFAULT_SCOPES = {
    "claude-code": "local",
    "claude-desktop": "user",
    "codex": "user",
    "gemini": "user",
    "cursor": "project",
  };

  var SYNC_EVENT = "lm-mcp-install:change";

  // Bind once on document/window — these listeners survive every SPA swap.
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKeydown);
  window.addEventListener(SYNC_EVENT, onBroadcast);

  // Apply saved state on first load and on every SPA nav.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applySavedState);
  } else {
    applySavedState();
  }
  document.addEventListener("gp-sphinx:navigated", applySavedState);

  function applySavedState() {
    var widgets = document.querySelectorAll(".lm-mcp-install");
    if (!widgets.length) return;
    var savedClient = localStorage.getItem(STORAGE.client);
    var savedMethod = localStorage.getItem(STORAGE.method);
    widgets.forEach(function (widget) {
      // Always re-select client (saved or server-default). Selecting the
      // client also restores that client's saved scope as a side effect
      // in `select()`, so we don't need a separate scope branch here.
      var clientValue = savedClient || ariaSelected(widget, "client");
      if (clientValue) {
        select(widget, "client", clientValue, { persist: false, broadcast: false });
      }
      if (savedMethod) {
        select(widget, "method", savedMethod, { persist: false, broadcast: false });
      }
      // Always sync — even if no localStorage entries existed, this is the
      // call that pushes the SSR defaults onto <html data-mcp-install-*>
      // so the prehydrate CSS rules have all three attrs to match against.
      syncHtmlAttrs(widget);
    });
  }

  function onClick(e) {
    var tab = e.target.closest(".lm-mcp-install__tab");
    if (!tab) return;
    var widget = tab.closest(".lm-mcp-install");
    if (!widget) return;
    select(widget, tab.dataset.tabKind, tab.dataset.tabValue, { persist: true, broadcast: true });
  }

  function onKeydown(e) {
    var tab = e.target.closest(".lm-mcp-install__tab");
    if (!tab) return;
    var widget = tab.closest(".lm-mcp-install");
    if (!widget) return;
    handleKeydown(e, widget, tab);
  }

  function onBroadcast(event) {
    document.querySelectorAll(".lm-mcp-install").forEach(function (widget) {
      if (widget === event.detail.origin) return;
      select(widget, event.detail.kind, event.detail.value, { persist: false, broadcast: false });
    });
  }

  function select(widget, kind, value, opts) {
    // Resolve which tabs to update for this kind. Scope tabs are grouped
    // per client via [data-tab-client], so we narrow to the active client.
    var tabSelector;
    if (kind === "scope") {
      var html0 = document.documentElement;
      var activeClient = html0.getAttribute("data-mcp-install-client")
        || ariaSelected(widget, "client");
      if (!activeClient) return;
      tabSelector =
        '.lm-mcp-install__tab[data-tab-kind="scope"]'
        + '[data-tab-client="' + activeClient + '"]';
    } else {
      tabSelector = '.lm-mcp-install__tab[data-tab-kind="' + kind + '"]';
    }

    var tabs = widget.querySelectorAll(tabSelector);
    var hasMatchingTab = false;
    tabs.forEach(function (tab) {
      var match = tab.dataset.tabValue === value;
      if (match) hasMatchingTab = true;
      tab.setAttribute("aria-selected", match ? "true" : "false");
      tab.setAttribute("tabindex", match ? "0" : "-1");
    });
    // For client/method, no matching tab means the value is unknown to this
    // widget — bail. For scope, single-scope clients (Claude Desktop) have
    // NO scope group rendered by the template, so tabs.length == 0 is the
    // expected steady state — fall through so syncHtmlAttrs can still push
    // the new client's default scope onto <html>.
    if (kind !== "scope" && !hasMatchingTab) return;

    if (kind === "client") {
      // Switching clients: also restore that client's saved scope (or
      // default) so the scope row updates atomically with the client change.
      var savedScope = localStorage.getItem(STORAGE.scope(value))
        || DEFAULT_SCOPES[value];
      if (savedScope) {
        select(widget, "scope", savedScope, { persist: false, broadcast: false });
      }
    }

    // Push the resulting widget state onto <html> so prehydrate CSS picks
    // the right tab, scope group, and panel. Doing this on every select()
    // keeps all three attrs in sync even when the user only clicks one tab
    // (the others read from the widget's existing aria-selected state).
    syncHtmlAttrs(widget);

    if (opts.persist) {
      if (kind === "scope") {
        var clientForScope = document.documentElement
          .getAttribute("data-mcp-install-client");
        if (clientForScope) {
          localStorage.setItem(STORAGE.scope(clientForScope), value);
        }
      } else {
        localStorage.setItem(STORAGE[kind], value);
      }
    }
    if (opts.broadcast) {
      window.dispatchEvent(
        new CustomEvent(SYNC_EVENT, {
          detail: { origin: widget, kind: kind, value: value },
        })
      );
    }
  }

  // Mirror the widget's current tab state onto <html> for all three
  // dimensions. The prehydrate CSS rules need every attr set for the
  // (client, method, scope) panel rule to match — leaving one unset
  // means the @layer hide rule hides the SSR default but no active
  // rule un-hides any panel, so the body paints empty.
  function syncHtmlAttrs(widget) {
    var html = document.documentElement;
    var client = ariaSelected(widget, "client");
    var method = ariaSelected(widget, "method");
    if (client) html.setAttribute("data-mcp-install-client", client);
    if (method) html.setAttribute("data-mcp-install-method", method);
    if (client) {
      var scopeTab = widget.querySelector(
        '.lm-mcp-install__tab[data-tab-kind="scope"]'
        + '[data-tab-client="' + client + '"]'
        + '[aria-selected="true"]'
      );
      var scope = scopeTab
        ? scopeTab.dataset.tabValue
        : (localStorage.getItem(STORAGE.scope(client)) || DEFAULT_SCOPES[client]);
      if (scope) html.setAttribute("data-mcp-install-scope", scope);
    }
  }

  function ariaSelected(widget, kind) {
    var tab = widget.querySelector(
      '.lm-mcp-install__tab[data-tab-kind="' + kind + '"][aria-selected="true"]'
    );
    return tab ? tab.dataset.tabValue : null;
  }

  function handleKeydown(event, widget, tab) {
    var kind = tab.dataset.tabKind;
    // Keep keyboard nav scoped to the visible group for scope tabs.
    var tabSelector;
    if (kind === "scope") {
      var client = tab.dataset.tabClient;
      tabSelector =
        '.lm-mcp-install__tab[data-tab-kind="scope"]'
        + '[data-tab-client="' + client + '"]';
    } else {
      tabSelector = '.lm-mcp-install__tab[data-tab-kind="' + kind + '"]';
    }
    var tabs = Array.prototype.slice.call(widget.querySelectorAll(tabSelector));
    var current = tabs.indexOf(tab);
    var next = current;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        next = (current + 1) % tabs.length;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        next = (current - 1 + tabs.length) % tabs.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = tabs.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    tabs[next].focus();
    select(widget, kind, tabs[next].dataset.tabValue, { persist: true, broadcast: true });
  }
})();
