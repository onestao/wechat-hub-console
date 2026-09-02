/* Native <dialog> lifecycle and accessibility helper. */

/**
 * Open a dialog as modal.
 * @param {HTMLDialogElement} dialog
 * @param {object} [options]
 * @param {boolean} [options.preventCancel]
 * @param {() => void} [options.onClose]
 */
export function openDialog(dialog, options = {}) {
  if (!dialog || typeof dialog.showModal !== "function") return;
  if (dialog.open) return;

  dialog.showModal();

  // Backdrop click to close (when click is directly on dialog boundary)
  const onClick = (e) => {
    if (options.preventCancel) return;
    const rect = dialog.getBoundingClientRect();
    const isOutside =
      e.clientX < rect.left ||
      e.clientX > rect.right ||
      e.clientY < rect.top ||
      e.clientY > rect.bottom;
    if (isOutside) {
      closeDialog(dialog);
    }
  };

  const onCancel = (e) => {
    if (options.preventCancel) {
      e.preventDefault();
    }
  };

  const onCloseInternal = () => {
    dialog.removeEventListener("click", onClick);
    dialog.removeEventListener("cancel", onCancel);
    dialog.removeEventListener("close", onCloseInternal);
    if (typeof options.onClose === "function") {
      options.onClose();
    }
  };

  dialog.addEventListener("click", onClick);
  dialog.addEventListener("cancel", onCancel);
  dialog.addEventListener("close", onCloseInternal);
}

/**
 * Close an open dialog.
 * @param {HTMLDialogElement} dialog
 */
export function closeDialog(dialog) {
  if (!dialog || !dialog.open) return;
  dialog.close();
}
