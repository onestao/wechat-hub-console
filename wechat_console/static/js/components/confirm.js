/* Action Confirmation Dialog.
 *
 * Replaces window.confirm() and window.alert() with accessible native <dialog>.
 */

import { escapeHtml } from "../format.js";
import { icon } from "../icons.js";
import { openDialog, closeDialog } from "./dialog.js";

let confirmDialogEl = null;
let currentResolver = null;

function ensureConfirmDialog() {
  if (confirmDialogEl) return confirmDialogEl;
  let el = document.getElementById("confirmDialog");
  if (!el) {
    el = document.createElement("dialog");
    el.id = "confirmDialog";
    el.className = "modal";
    document.body.appendChild(el);
  }
  confirmDialogEl = el;
  return el;
}

/**
 * Request user confirmation for an action.
 * @param {object} options
 * @param {string} options.title
 * @param {string} options.text
 * @param {string} [options.confirmLabel="确认"]
 * @param {string} [options.cancelLabel="取消"]
 * @param {"danger"|"warning"|"primary"} [options.tone="primary"]
 * @returns {Promise<boolean>}
 */
export function confirmAction({
  title = "请确认",
  text = "",
  confirmLabel = "确认",
  cancelLabel = "取消",
  tone = "primary",
} = {}) {
  const dialog = ensureConfirmDialog();

  if (currentResolver) {
    currentResolver(false);
    currentResolver = null;
  }

  return new Promise((resolve) => {
    currentResolver = resolve;

    const btnClass =
      tone === "danger"
        ? "btn btn-danger"
        : tone === "warning"
        ? "btn btn-secondary"
        : "btn btn-primary";

    dialog.innerHTML = `
      <div class="modal-shell">
        <div class="modal-head">
          <div class="modal-head-text">
            <div class="modal-title">${escapeHtml(title)}</div>
          </div>
          <button class="btn btn-icon" id="confirmCloseBtn" aria-label="关闭">
            ${icon("close")}
          </button>
        </div>
        <div class="modal-body">
          <p style="font-size: var(--fs-body); color: var(--text-secondary); line-height: var(--lh-body); margin: 0;">
            ${escapeHtml(text)}
          </p>
        </div>
        <div class="modal-foot">
          <button class="btn btn-ghost" id="confirmCancelBtn">${escapeHtml(cancelLabel)}</button>
          <button class="${btnClass}" id="confirmOkBtn">${escapeHtml(confirmLabel)}</button>
        </div>
      </div>
    `;

    const cleanup = (result) => {
      if (currentResolver) {
        currentResolver(result);
        currentResolver = null;
      }
      closeDialog(dialog);
    };

    dialog.querySelector("#confirmOkBtn").onclick = () => cleanup(true);
    dialog.querySelector("#confirmCancelBtn").onclick = () => cleanup(false);
    dialog.querySelector("#confirmCloseBtn").onclick = () => cleanup(false);

    openDialog(dialog, {
      onClose: () => {
        if (currentResolver) {
          currentResolver(false);
          currentResolver = null;
        }
      },
    });
  });
}
