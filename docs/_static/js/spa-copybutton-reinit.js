/**
 * Re-attach copy buttons to ``.admonition.prompt > p:last-child`` after
 * gp-sphinx's SPA DOM swap.
 *
 * Context:
 *
 * - ``copybutton_selector`` in ``docs/conf.py`` is
 *   ``"div.highlight pre, div.admonition.prompt > p:last-child"`` — we copy
 *   *prompt text* as well as code.
 * - On full-page load, ``sphinx-copybutton`` iterates that selector and
 *   inserts a ``.copybtn`` after every match, then binds
 *   ``new ClipboardJS('.copybtn', ...)``. ClipboardJS uses delegated
 *   listening on ``document.body``, so those clicks keep working across
 *   SPA DOM swaps.
 * - On SPA navigation, gp-sphinx's ``spa-nav.js::addCopyButtons`` iterates
 *   ``"div.highlight pre"`` only — it does NOT re-attach buttons to
 *   ``.admonition.prompt > p:last-child``. After an SPA swap, pages like
 *   ``/recipes/`` (prompt-heavy, no code blocks) render naked: no copy
 *   affordance at all.
 *
 * This shim: capture the first ``.copybtn`` that appears anywhere in the
 * document as a reusable template (so we pick up ``sphinx-copybutton``'s
 * locale-specific tooltip and icon exactly), then after every SPA swap
 * re-insert buttons on prompt-admonition ``<p>`` elements that lack a
 * ``.copybtn`` sibling. Because the inserted elements have
 * ``class="copybtn"`` and a ``data-clipboard-target`` pointing to a
 * ``<p>`` with a matching ``id``, they plug into ClipboardJS's
 * body-delegated listener transparently and behave identically to
 * initially-rendered buttons.
 *
 * ``FALLBACK_COPYBTN_HTML`` covers the rare case where the user's first
 * page has no ``.copybtn`` anywhere (e.g. a landing page with no code
 * blocks and no prompt admonitions) — the fallback button is a bare
 * ``.copybtn`` with the same MDI "content-copy" icon upstream
 * ``sphinx-copybutton`` ships. Ugly if tooltip styling needs the exact
 * template but functional for clicks.
 *
 * The correct upstream fix is in gp-sphinx — its ``addCopyButtons``
 * should iterate the full ``copybutton_selector`` (or dispatch a
 * ``spa-nav-complete`` event that consumers like ``sphinx-copybutton``
 * can hook). Until then, this project-local shim keeps the docs
 * behaving.
 */
(function () {
  "use strict";

  if (!window.MutationObserver) return;

  var PROMPT_TARGET = ".admonition.prompt > p:last-child";
  var FALLBACK_COPYBTN_HTML =
    '<button class="copybtn o-tooltip--left" data-tooltip="Copy">' +
    '<svg xmlns="http://www.w3.org/2000/svg" class="icon" viewBox="0 0 24 24">' +
    '<title>Copy</title>' +
    '<path fill="currentColor" d="M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z"/>' +
    "</svg></button>";

  var copyBtnTemplate = null;
  var idCounter = 0;

  function ensureTemplate() {
    if (copyBtnTemplate) return true;
    var live = document.querySelector(".copybtn");
    if (live) {
      copyBtnTemplate = live.cloneNode(true);
      copyBtnTemplate.classList.remove("success");
      copyBtnTemplate.removeAttribute("data-clipboard-target");
      return true;
    }
    // Fallback: no live .copybtn on page — fabricate from known markup.
    var holder = document.createElement("div");
    holder.innerHTML = FALLBACK_COPYBTN_HTML;
    copyBtnTemplate = holder.firstChild;
    return true;
  }

  function ensurePromptButtons() {
    if (!ensureTemplate()) return;
    document.querySelectorAll(PROMPT_TARGET).forEach(function (p) {
      var next = p.nextElementSibling;
      if (next && next.classList && next.classList.contains("copybtn")) {
        return;
      }
      if (!p.id) {
        p.id = "mcp-promptcell-" + idCounter;
        idCounter += 1;
      }
      var btn = copyBtnTemplate.cloneNode(true);
      btn.classList.remove("success");
      btn.setAttribute("data-clipboard-target", "#" + p.id);
      p.insertAdjacentElement("afterend", btn);
    });
  }

  // Observer has two jobs:
  //   (a) capture the template the instant sphinx-copybutton inserts its
  //       first ``.copybtn`` (happens at DOMContentLoaded, regardless of
  //       listener-registration order vs our own);
  //   (b) detect SPA-swap completion (a subtree addition that contains a
  //       ``.admonition.prompt``) and re-insert prompt buttons.
  new MutationObserver(function (records) {
    var sawCopybtn = false;
    var sawArticle = false;
    for (var i = 0; i < records.length; i += 1) {
      var added = records[i].addedNodes;
      for (var j = 0; j < added.length; j += 1) {
        var n = added[j];
        if (n.nodeType !== 1) continue;
        var cls = n.classList;
        if (cls && cls.contains("copybtn")) sawCopybtn = true;
        if (cls && cls.contains("admonition") && cls.contains("prompt")) {
          sawArticle = true;
        }
        if (n.querySelector) {
          if (!sawCopybtn && n.querySelector(".copybtn")) sawCopybtn = true;
          if (!sawArticle && n.querySelector(".admonition.prompt")) {
            sawArticle = true;
          }
        }
      }
    }
    if (sawCopybtn) ensureTemplate();
    if (sawArticle) ensurePromptButtons();
  }).observe(document.body, { childList: true, subtree: true });

  // Initial-load pass — MUST run after sphinx-copybutton has had its own
  // DOMContentLoaded handler attach its buttons, otherwise our fallback
  // template beats sphinx-copybutton's localized one to the punch on
  // prompt-only pages like ``/recipes/``. At deferred-script execution
  // time ``readyState`` is ``"interactive"`` (parse done, DOMContentLoaded
  // not yet fired), so register a listener instead of running eagerly.
  // ``"complete"`` means everything has already fired — safe to run now.
  if (document.readyState === "complete") {
    ensurePromptButtons();
  } else {
    document.addEventListener("DOMContentLoaded", ensurePromptButtons);
  }
})();
