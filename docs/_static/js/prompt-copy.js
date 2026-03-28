/**
 * Markdown-preserving copy for prompt blocks.
 *
 * sphinx-copybutton uses innerText which strips HTML. This script
 * intercepts copy on .admonition.prompt buttons and reconstructs
 * inline markdown (backtick-wrapping <code> elements) before copying.
 *
 * Uses event delegation on document (capture phase) so it survives
 * SPA navigation DOM swaps without re-initialization.
 */
(function () {
  // Same green checkmark SVG that sphinx-copybutton uses (copybutton.js:61-65)
  var iconCheck =
    '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-check" width="44" height="44" viewBox="0 0 24 24" stroke-width="2" stroke="#22863a" fill="none" stroke-linecap="round" stroke-linejoin="round">' +
    "<title>Copied!</title>" +
    '<path stroke="none" d="M0 0h24v24H0z" fill="none"/>' +
    '<path d="M5 12l5 5l10 -10" />' +
    "</svg>";

  function toMarkdown(el) {
    var text = "";
    for (var i = 0; i < el.childNodes.length; i++) {
      var node = el.childNodes[i];
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent;
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.tagName === "CODE") {
          text += "`" + node.textContent + "`";
        } else {
          text += toMarkdown(node);
        }
      }
    }
    return text;
  }

  // Match sphinx-copybutton's exact feedback:
  // icon swap + tooltip + .success class with staggered timeouts
  function showCopySuccess(btn) {
    var savedIcon = btn.innerHTML;
    btn.innerHTML = iconCheck;
    btn.setAttribute("data-tooltip", "Copied!");
    btn.classList.add("success");
    setTimeout(function () {
      btn.classList.remove("success");
    }, 1500);
    setTimeout(function () {
      btn.innerHTML = savedIcon;
      btn.setAttribute("data-tooltip", "Copy");
    }, 2000);
  }

  // Single delegated listener on document (capture phase).
  // Runs before ClipboardJS's bubble-phase delegation.
  // Detects prompt buttons by checking where their data-clipboard-target
  // points, not by button ancestry (buttons are siblings of the prompt
  // div, not descendants — inserted by insertAdjacentHTML('afterend')).
  document.addEventListener(
    "click",
    function (e) {
      var btn = e.target.closest(".copybtn");
      if (!btn) return;

      var targetId = btn.getAttribute("data-clipboard-target");
      if (!targetId) {
        console.warn("prompt-copy: copybtn has no data-clipboard-target");
        return;
      }

      var target = document.querySelector(targetId);
      if (!target) {
        console.warn("prompt-copy: target not found:", targetId);
        return;
      }

      // Only intercept if the target is inside a prompt admonition
      if (!target.closest("div.admonition.prompt")) return;

      e.stopImmediatePropagation();
      e.preventDefault();

      var markdown = toMarkdown(target);
      navigator.clipboard.writeText(markdown).then(
        function () {
          showCopySuccess(btn);
        },
        function (err) {
          console.warn("prompt-copy: clipboard write failed:", err);
          btn.setAttribute("data-tooltip", "Failed to copy");
          setTimeout(function () {
            btn.setAttribute("data-tooltip", "Copy");
          }, 2000);
        }
      );
    },
    true
  );
})();
