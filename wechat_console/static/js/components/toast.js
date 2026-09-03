/* Toast notification system. */

import { escapeHtml } from "../format.js";
import { icon } from "../icons.js";

let stackEl = null;

function ensureStack() {
  if (stackEl && document.body.contains(stackEl)) return stackEl;
  let el = document.querySelector(".toast-stack");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast-stack";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  stackEl = el;
  return el;
}

/**
 * Show a toast notification.
 * @param {object} options
 * @param {string} options.title
 * @param {string} [options.text=""]
 * @param {"good"|"bad"|"warn"|"info"} [options.tone="good"]
 * @param {number} [options.duration=4000]
 */
export function toast(options = {}) {
  if (typeof options === "string") {
    options = { title: options, tone: arguments[1] || "good" };
  }
  const {
    title = "",
    text = "",
    tone = "good",
    duration = 4000,
  } = options;
  const stack = ensureStack();
  const toastEl = document.createElement("div");
  toastEl.className = "toast";
  if (tone) toastEl.dataset.tone = tone;

  const iconName =
    tone === "good"
      ? "check"
      : tone === "bad"
      ? "alertCircle"
      : tone === "warn"
      ? "alertTriangle"
      : "info";

  toastEl.innerHTML = `
    <div class="toast-icon">${icon(iconName, { size: "sm" })}</div>
    <div class="toast-body">
      ${title ? `<div class="toast-title">${escapeHtml(title)}</div>` : ""}
      ${text ? `<div class="toast-text">${escapeHtml(text)}</div>` : ""}
    </div>
    <button class="toast-close" aria-label="关闭提示">${icon("close", { size: "sm" })}</button>
  `;

  let timer = null;
  const remove = () => {
    if (timer) clearTimeout(timer);
    toastEl.style.opacity = "0";
    toastEl.style.transform = "translateY(8px)";
    setTimeout(() => {
      if (toastEl.parentNode) {
        toastEl.parentNode.removeChild(toastEl);
      }
    }, 200);
  };

  toastEl.querySelector(".toast-close").onclick = remove;

  stack.appendChild(toastEl);

  if (duration > 0) {
    timer = setTimeout(remove, duration);
  }
}
