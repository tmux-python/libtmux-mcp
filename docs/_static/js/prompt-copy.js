/**
 * Markdown-preserving copy for prompt blocks.
 *
 * sphinx-copybutton uses innerText which strips HTML. This script
 * intercepts copy on .admonition.prompt buttons and reconstructs
 * inline markdown (backtick-wrapping <code> elements) before copying.
 */
(function () {
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

  function init() {
    var buttons = document.querySelectorAll(
      "div.admonition.prompt button.copybtn"
    );
    buttons.forEach(function (btn) {
      btn.addEventListener(
        "click",
        function (e) {
          e.stopImmediatePropagation();
          e.preventDefault();

          var targetId = btn.getAttribute("data-clipboard-target");
          var target = document.querySelector(targetId);
          if (!target) return;

          var markdown = toMarkdown(target);
          navigator.clipboard.writeText(markdown).then(function () {
            btn.setAttribute("data-tooltip", "Copied!");
            btn.classList.add("success");
            setTimeout(function () {
              btn.setAttribute("data-tooltip", "Copy");
              btn.classList.remove("success");
            }, 2000);
          });
        },
        true
      );
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(init, 100);
    });
  } else {
    setTimeout(init, 100);
  }
})();
