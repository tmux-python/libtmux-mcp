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
 *
 * Cooldown state is split into three orthogonal axes:
 *   - `cooldown.enabled` ("1" | "0"): master on/off
 *   - `cooldown.type`    ("days" | "bypass"): cooldown flavor when enabled
 *   - `cooldown.days`    int: day count when type=days
 *
 * The checkbox in the method-tab row flips `enabled` only — it does *not*
 * change the install/settings view. The "Configure cooldowns" label is the
 * only entry point to the settings view. Inside settings, touching the
 * radio or days input auto-sets enabled=1 (implicit activation).
 *
 * Days slots come in two flavors — uvx and pip days bodies embed a
 * duration slot (`P<N>D`, self-refreshing in both uv and pip 26.1+),
 * while pipx days bodies embed an absolute-date slot (pipx 1.8.0's
 * bundled pip <26.1 rejects duration). Both are updated on every days
 * input change.
 *
 * Vanilla JS, no deps.
 */
(function () {
  "use strict";

  var STORAGE = {
    client: "libtmux-mcp.mcp-install.client",
    method: "libtmux-mcp.mcp-install.method",
    scope: function (client) { return "libtmux-mcp.mcp-install.scope." + client; },
    cooldownEnabled: "libtmux-mcp.mcp-install.cooldown.enabled",
    cooldownType: "libtmux-mcp.mcp-install.cooldown.type",
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

  var DEFAULT_COOLDOWN_ENABLED = false;
  var DEFAULT_COOLDOWN_TYPE = "days";
  var DEFAULT_COOLDOWN_DAYS = 7;
  var VALID_COOLDOWN_TYPES = { days: 1, bypass: 1 };

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
    var enabled = readCooldownEnabled();
    var type = readCooldownType();
    var days = readCooldownDays();
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
      applyCooldownToWidget(widget, enabled, type, days);
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
    // Tab clicks are an install-side action — if the user was mid-
    // configuring cooldowns, the click implies they want to see the
    // snippet for their new selection. Return to install view so the
    // updated panel is visible.
    setView("install");
  }

  function handleCooldownAction(widget, el, event) {
    var action = el.dataset.action;
    if (action === "cooldown-toggle") {
      // Native checkbox change runs through onChange. Don't double-handle.
      return false;
    }
    if (action === "cooldown-open") {
      var current = document.documentElement.getAttribute("data-mcp-install-view");
      setView(current === "settings" ? "install" : "settings");
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
      // Master on/off only. Does NOT change view. Does NOT change
      // type or days. Just flips ``cooldown.enabled``.
      setCooldownEnabled(el.checked, { persist: true, broadcast: true });
      return;
    }
    if (action === "cooldown-mode") {
      // Radio selection in settings -> set type, auto-enable.
      setCooldownType(el.value, { persist: true, broadcast: true });
      if (!readCooldownEnabled()) {
        setCooldownEnabled(true, { persist: true, broadcast: true });
      }
      return;
    }
    if (action === "cooldown-days") {
      // Days input in settings -> set days, auto-enable, force type=days.
      var n = clampDays(parseInt(el.value, 10));
      setCooldownDays(n, { persist: true, broadcast: true });
      if (readCooldownType() !== "days") {
        setCooldownType("days", { persist: true, broadcast: true });
      }
      if (!readCooldownEnabled()) {
        setCooldownEnabled(true, { persist: true, broadcast: true });
      }
    }
  }

  function onInput(e) {
    // ``input`` fires on every keystroke for number inputs. We mirror the
    // computed duration + cutoff date into every slot span so the snippet
    // updates in real time even before the user blurs the field.
    // localStorage write happens only on ``change`` (see onChange) to
    // avoid hammering writes.
    var el = e.target.closest('[data-action="cooldown-days"]');
    if (!el) return;
    var widget = el.closest(".lm-mcp-install");
    if (!widget) return;
    var n = parseInt(el.value, 10);
    if (!isNaN(n) && n >= 1) {
      updateAllCooldownSlots(n);
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
      var kind = event.detail.kind;
      if (kind === "cooldown-enabled" || kind === "cooldown-type" || kind === "cooldown-days") {
        applyCooldownToWidget(
          widget,
          readCooldownEnabled(),
          readCooldownType(),
          readCooldownDays()
        );
        return;
      }
      select(widget, kind, event.detail.value, { persist: false, broadcast: false });
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

  function readCooldownEnabled() {
    var v = localStorage.getItem(STORAGE.cooldownEnabled);
    if (v === "1") return true;
    if (v === "0") return false;
    return DEFAULT_COOLDOWN_ENABLED;
  }

  function readCooldownType() {
    var t = localStorage.getItem(STORAGE.cooldownType);
    return VALID_COOLDOWN_TYPES[t] ? t : DEFAULT_COOLDOWN_TYPE;
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

  function setCooldownEnabled(enabled, opts) {
    var v = enabled ? "1" : "0";
    document.documentElement.setAttribute("data-mcp-install-cooldown-enabled", v);
    if (opts.persist) localStorage.setItem(STORAGE.cooldownEnabled, v);
    document.querySelectorAll(".lm-mcp-install").forEach(function (widget) {
      applyCooldownToWidget(widget, enabled, readCooldownType(), readCooldownDays());
    });
    if (opts.broadcast) {
      window.dispatchEvent(
        new CustomEvent(SYNC_EVENT, {
          detail: { origin: null, kind: "cooldown-enabled", value: v },
        })
      );
    }
  }

  function setCooldownType(type, opts) {
    if (!VALID_COOLDOWN_TYPES[type]) return;
    document.documentElement.setAttribute("data-mcp-install-cooldown-type", type);
    if (opts.persist) localStorage.setItem(STORAGE.cooldownType, type);
    document.querySelectorAll(".lm-mcp-install").forEach(function (widget) {
      applyCooldownToWidget(widget, readCooldownEnabled(), type, readCooldownDays());
    });
    if (opts.broadcast) {
      window.dispatchEvent(
        new CustomEvent(SYNC_EVENT, {
          detail: { origin: null, kind: "cooldown-type", value: type },
        })
      );
    }
  }

  function setCooldownDays(days, opts) {
    var n = clampDays(days);
    document.documentElement.setAttribute("data-mcp-install-cooldown-days", String(n));
    if (opts.persist) localStorage.setItem(STORAGE.cooldownDays, String(n));
    updateAllCooldownSlots(n);
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
    // YYYY-MM-DD in UTC, for pipx (pipx 1.8.0's bundled pip <26.1 rejects
    // the duration form). uv and pip 26.1+ both accept this absolute form
    // too, but the duration form is preferred for them because it stays
    // self-refreshing in the user's saved MCP config.
    var ms = Date.now() - n * 86400000;
    return new Date(ms).toISOString().slice(0, 10);
  }

  function daysToIsoDuration(n) {
    // P<N>D in ISO 8601. Used by uvx and pip days panels.
    return "P" + n + "D";
  }

  function updateAllCooldownSlots(n) {
    var duration = daysToIsoDuration(n);
    var date = daysToIsoDate(n);
    document.querySelectorAll("[data-cooldown-duration-slot]").forEach(function (slot) {
      slot.textContent = duration;
    });
    document.querySelectorAll("[data-cooldown-date-slot]").forEach(function (slot) {
      slot.textContent = date;
    });
  }

  function applyCooldownToWidget(widget, enabled, type, days) {
    // Checkbox: checked iff enabled.
    var toggle = widget.querySelector(".lm-mcp-install__cooldown-toggle");
    if (toggle) toggle.checked = !!enabled;
    // Radio: the matching radio in the settings form.
    widget.querySelectorAll('[data-action="cooldown-mode"]').forEach(function (radio) {
      radio.checked = radio.value === type;
    });
    // Days input value (don't clobber while typing).
    var daysInput = widget.querySelector('[data-action="cooldown-days"]');
    if (daysInput && document.activeElement !== daysInput) {
      daysInput.value = String(days);
    }
    // Slot contents for this widget's panels (other widgets get updated
    // by their own applyCooldownToWidget call). Both duration and date
    // slots are refreshed — only one kind is visible per panel.
    var duration = daysToIsoDuration(days);
    var date = daysToIsoDate(days);
    widget.querySelectorAll("[data-cooldown-duration-slot]").forEach(function (slot) {
      slot.textContent = duration;
    });
    widget.querySelectorAll("[data-cooldown-date-slot]").forEach(function (slot) {
      slot.textContent = date;
    });
  }

  function setView(view) {
    var prev = document.documentElement.getAttribute("data-mcp-install-view");
    if (prev === view) return;
    document.documentElement.setAttribute("data-mcp-install-view", view);
  }
})();
