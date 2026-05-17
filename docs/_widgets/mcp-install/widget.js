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
 * Scope is per-client (storage key `libtmux-mcp.mcp-install.scope.<id>`).
 * Cooldown is global (storage keys `libtmux-mcp.mcp-install.cooldown.mode`
 * and `.cooldown.days`). The cooldown days value is also reflected into
 * every `[data-cooldown-days-slot]` span's textContent on save so the
 * rendered snippet stays copy-pasteable.
 *
 * Vanilla JS, no deps.
 */
(function () {
  "use strict";

  var STORAGE = {
    client: "libtmux-mcp.mcp-install.client",
    method: "libtmux-mcp.mcp-install.method",
    scope: function (client) { return "libtmux-mcp.mcp-install.scope." + client; },
    cooldownMode: "libtmux-mcp.mcp-install.cooldown.mode",
    cooldownDays: "libtmux-mcp.mcp-install.cooldown.days",
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

  var DEFAULT_COOLDOWN_MODE = "off";
  var DEFAULT_COOLDOWN_DAYS = 7;
  var VALID_COOLDOWN_MODES = { off: 1, days: 1, bypass: 1 };

  var SYNC_EVENT = "lm-mcp-install:change";

  // Bind once on document/window — these listeners survive every SPA swap.
  document.addEventListener("click", onClick);
  document.addEventListener("change", onChange);
  document.addEventListener("input", onInput);
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
    var savedMode = readCooldownMode();
    var savedDays = readCooldownDays();
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
      // Cooldown state: paint UI + slot text from the same saved values
      // the prehydrate script already pushed onto <html>.
      applyCooldownToWidget(widget, savedMode, savedDays);
    });
    // Settings view is transient — always reset to install view on
    // first load and every SPA nav. (The user wouldn't expect to land
    // mid-form on a new page.)
    setView("install");
  }

  function onClick(e) {
    var action = e.target.closest("[data-action]");
    if (action) {
      var widget = action.closest(".lm-mcp-install");
      if (widget) {
        if (handleCooldownAction(widget, action, e)) return;
      }
    }
    var tab = e.target.closest(".lm-mcp-install__tab");
    if (!tab) return;
    var tabWidget = tab.closest(".lm-mcp-install");
    if (!tabWidget) return;
    select(tabWidget, tab.dataset.tabKind, tab.dataset.tabValue, { persist: true, broadcast: true });
  }

  function handleCooldownAction(widget, el, event) {
    var action = el.dataset.action;
    if (action === "cooldown-toggle") {
      // Native checkbox change runs through onChange. Don't double-handle.
      return false;
    }
    if (action === "cooldown-open") {
      setView("settings");
      event.preventDefault();
      return true;
    }
    if (action === "cooldown-help") {
      setView("settings");
      var details = widget.querySelector(".lm-mcp-install__cooldown-explainer");
      if (details) details.open = true;
      event.preventDefault();
      return true;
    }
    if (action === "cooldown-back") {
      setView("install");
      event.preventDefault();
      return true;
    }
    return false;
  }

  function onChange(e) {
    var el = e.target.closest("[data-action]");
    if (!el) return;
    var widget = el.closest(".lm-mcp-install");
    if (!widget) return;
    var action = el.dataset.action;
    if (action === "cooldown-toggle") {
      var nextMode = el.checked
        ? (readCooldownMode() !== "off" ? readCooldownMode() : "days")
        : "off";
      setCooldownMode(nextMode, { persist: true, broadcast: true });
      if (el.checked) setView("settings");
      else setView("install");
      return;
    }
    if (action === "cooldown-mode") {
      setCooldownMode(el.value, { persist: true, broadcast: true });
      return;
    }
    if (action === "cooldown-days") {
      var n = clampDays(parseInt(el.value, 10));
      setCooldownDays(n, { persist: true, broadcast: true });
      // Also flip mode to "days" so the snippet reflects the input.
      if (readCooldownMode() !== "days") {
        setCooldownMode("days", { persist: true, broadcast: true });
      }
    }
  }

  function onInput(e) {
    // ``input`` fires on every keystroke for number inputs. We mirror
    // the computed cutoff date into every slot span so the snippet
    // updates in real time even before the user blurs the field.
    // localStorage write happens only on ``change`` (see onChange) to
    // avoid hammering writes.
    var el = e.target.closest('[data-action="cooldown-days"]');
    if (!el) return;
    var widget = el.closest(".lm-mcp-install");
    if (!widget) return;
    var n = parseInt(el.value, 10);
    if (!isNaN(n) && n >= 1) {
      updateAllCooldownDateSlots(n);
      document.documentElement.setAttribute("data-mcp-install-cooldown-days", String(n));
    }
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
      if (event.detail.kind === "cooldown-mode") {
        applyCooldownToWidget(widget, event.detail.value, readCooldownDays());
        return;
      }
      if (event.detail.kind === "cooldown-days") {
        applyCooldownToWidget(widget, readCooldownMode(), event.detail.value);
        return;
      }
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

  // -------- cooldown helpers --------------------------------------------

  function readCooldownMode() {
    var m = localStorage.getItem(STORAGE.cooldownMode);
    return VALID_COOLDOWN_MODES[m] ? m : DEFAULT_COOLDOWN_MODE;
  }

  function readCooldownDays() {
    var d = parseInt(localStorage.getItem(STORAGE.cooldownDays), 10);
    return clampDays(d);
  }

  function clampDays(n) {
    if (isNaN(n)) return DEFAULT_COOLDOWN_DAYS;
    if (n < 1) return 1;
    if (n > 365) return 365;
    return n;
  }

  function setCooldownMode(mode, opts) {
    if (!VALID_COOLDOWN_MODES[mode]) return;
    document.documentElement.setAttribute("data-mcp-install-cooldown-mode", mode);
    if (opts.persist) localStorage.setItem(STORAGE.cooldownMode, mode);
    // Sync every widget's UI: checkbox, radio, slot text contents.
    document.querySelectorAll(".lm-mcp-install").forEach(function (widget) {
      applyCooldownToWidget(widget, mode, readCooldownDays());
    });
    if (opts.broadcast) {
      window.dispatchEvent(
        new CustomEvent(SYNC_EVENT, {
          detail: { origin: null, kind: "cooldown-mode", value: mode },
        })
      );
    }
  }

  function setCooldownDays(days, opts) {
    var n = clampDays(days);
    document.documentElement.setAttribute("data-mcp-install-cooldown-days", String(n));
    if (opts.persist) localStorage.setItem(STORAGE.cooldownDays, String(n));
    updateAllCooldownDateSlots(n);
    // Sync the days input across every widget (multi-widget page).
    document.querySelectorAll('[data-action="cooldown-days"]').forEach(function (input) {
      if (document.activeElement !== input) input.value = String(n);
    });
    if (opts.broadcast) {
      window.dispatchEvent(
        new CustomEvent(SYNC_EVENT, {
          detail: { origin: null, kind: "cooldown-days", value: n },
        })
      );
    }
  }

  function daysToIsoDate(n) {
    // YYYY-MM-DD in UTC. We use an absolute date rather than ISO 8601
    // duration (P<N>D) because pipx 1.8.0 bundles a pip older than 26.1,
    // which rejects the duration syntax. Absolute dates work in uv,
    // pip 26.0+, and pipx's bundled pip — portable across the matrix.
    var ms = Date.now() - n * 86400000;
    return new Date(ms).toISOString().slice(0, 10);
  }

  function updateAllCooldownDateSlots(n) {
    var iso = daysToIsoDate(n);
    document.querySelectorAll("[data-cooldown-date-slot]").forEach(function (slot) {
      slot.textContent = iso;
    });
  }

  function applyCooldownToWidget(widget, mode, days) {
    // Checkbox: checked iff mode != "off".
    var toggle = widget.querySelector(".lm-mcp-install__cooldown-toggle");
    if (toggle) toggle.checked = mode !== "off";
    // Radio: the matching radio in the settings form.
    widget.querySelectorAll('[data-action="cooldown-mode"]').forEach(function (radio) {
      radio.checked = radio.value === mode;
    });
    // Days input value (don't clobber while typing).
    var daysInput = widget.querySelector('[data-action="cooldown-days"]');
    if (daysInput && document.activeElement !== daysInput) {
      daysInput.value = String(days);
    }
    // Cooldown date slots inside snippets (this widget's panels only —
    // other widgets get updated by their own applyCooldownToWidget call in
    // applySavedState).
    var iso = daysToIsoDate(days);
    widget.querySelectorAll("[data-cooldown-date-slot]").forEach(function (slot) {
      slot.textContent = iso;
    });
  }

  function setView(view) {
    var prev = document.documentElement.getAttribute("data-mcp-install-view");
    if (prev === view) return;
    document.documentElement.setAttribute("data-mcp-install-view", view);
  }
})();
