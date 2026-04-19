/* MCP install widget — SPA-safe tab sync + localStorage persistence.
 *
 * Uses document-level event delegation so listeners survive gp-sphinx SPA
 * navigation (which swaps .article-container via .replaceWith()). Saved
 * localStorage state is re-applied on DOMContentLoaded and on every
 * gp-sphinx:navigated event (see sphinx-gp-theme's README for the contract).
 *
 * Vanilla JS, no deps.
 */
(function () {
  "use strict";

  var STORAGE = {
    client: "libtmux-mcp.mcp-install.client",
    method: "libtmux-mcp.mcp-install.method",
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
    var saved = {
      client: localStorage.getItem(STORAGE.client),
      method: localStorage.getItem(STORAGE.method),
    };
    widgets.forEach(function (widget) {
      if (saved.client) select(widget, "client", saved.client, { persist: false, broadcast: false });
      if (saved.method) select(widget, "method", saved.method, { persist: false, broadcast: false });
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
    var tabs = widget.querySelectorAll('.lm-mcp-install__tab[data-tab-kind="' + kind + '"]');
    var found = false;
    tabs.forEach(function (tab) {
      var match = tab.dataset.tabValue === value;
      if (match) found = true;
      tab.setAttribute("aria-selected", match ? "true" : "false");
      tab.setAttribute("tabindex", match ? "0" : "-1");
    });
    if (!found) return; // value not available in this widget — ignore.

    updatePanels(widget);

    if (opts.persist) localStorage.setItem(STORAGE[kind], value);
    if (opts.broadcast) {
      window.dispatchEvent(
        new CustomEvent(SYNC_EVENT, {
          detail: { origin: widget, kind: kind, value: value },
        })
      );
    }
  }

  function updatePanels(widget) {
    var client = selectedValue(widget, "client");
    var method = selectedValue(widget, "method");
    widget.querySelectorAll(".lm-mcp-install__panel").forEach(function (panel) {
      var match = panel.dataset.client === client && panel.dataset.method === method;
      if (match) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
    });
  }

  function selectedValue(widget, kind) {
    var tab = widget.querySelector(
      '.lm-mcp-install__tab[data-tab-kind="' + kind + '"][aria-selected="true"]'
    );
    return tab ? tab.dataset.tabValue : null;
  }

  function handleKeydown(event, widget, tab) {
    var kind = tab.dataset.tabKind;
    var tabs = Array.prototype.slice.call(
      widget.querySelectorAll('.lm-mcp-install__tab[data-tab-kind="' + kind + '"]')
    );
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
