/* Context Menu & Action Sheet Component.
 *
 * Desktop: Popover menu attached to trigger.
 * Mobile (<=767px): Native bottom sheet dialog.
 */

import { escapeHtml } from "../format.js";
import { icon } from "../icons.js";
import { openDialog, closeDialog } from "./dialog.js";

let currentPopover = null;
let currentSheet = null;
let popoverCleanup = null;

/**
 * Show action menu.
 * @param {HTMLElement} triggerEl
 * @param {Array<{action?: string, label?: string, icon?: string, tone?: string, disabled?: boolean, disabledReason?: string, divider?: boolean}>} items
 * @param {(action: string, item: any) => void} onSelect
 * @param {object} [options]
 * @param {string} [options.title="更多操作"]
 */
export function showMenu(triggerEl, items, onSelect, { title = "更多操作" } = {}) {
  closeActiveMenu();

  const isMobile = window.innerWidth <= 767;

  if (isMobile) {
    showMobileSheet(triggerEl, items, onSelect, title);
  } else {
    showDesktopPopover(triggerEl, items, onSelect);
  }
}

export function closeActiveMenu() {
  if (popoverCleanup) {
    popoverCleanup();
    popoverCleanup = null;
  }
  if (currentPopover && currentPopover.parentNode) {
    currentPopover.parentNode.removeChild(currentPopover);
    currentPopover = null;
  }
  if (currentSheet) {
    closeDialog(currentSheet);
    currentSheet = null;
  }
}

function renderMenuItems(items, onSelect, onClose) {
  const fragment = document.createDocumentFragment();
  const menu = document.createElement("div");
  menu.className = "menu";

  for (const item of items) {
    if (item.divider) {
      const div = document.createElement("div");
      div.className = "menu-divider";
      menu.appendChild(div);
      continue;
    }

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "menu-item";
    if (item.tone) btn.dataset.tone = item.tone;
    if (item.disabled) {
      btn.disabled = true;
      if (item.disabledReason) btn.title = item.disabledReason;
    }

    const iconHtml = item.icon ? icon(item.icon, { size: "sm" }) : "";
    btn.innerHTML = `${iconHtml}<span>${escapeHtml(item.label || "")}</span>`;

    btn.onclick = (e) => {
      e.stopPropagation();
      if (item.disabled) return;
      onClose();
      if (typeof onSelect === "function") {
        onSelect(item.action, item);
      }
    };

    menu.appendChild(btn);
  }

  fragment.appendChild(menu);
  return fragment;
}

function showDesktopPopover(triggerEl, items, onSelect) {
  const popover = document.createElement("div");
  popover.className = "popover";
  popover.style.position = "fixed";
  popover.style.zIndex = "60";

  const close = () => {
    closeActiveMenu();
    triggerEl?.focus?.();
  };

  popover.appendChild(renderMenuItems(items, onSelect, close));
  document.body.appendChild(popover);
  currentPopover = popover;

  // Position popover
  const rect = triggerEl.getBoundingClientRect();
  const popRect = popover.getBoundingClientRect();

  let top = rect.bottom + 4;
  let left = rect.right - popRect.width;

  // Ensure within viewport
  if (left < 8) left = 8;
  if (top + popRect.height > window.innerHeight - 8) {
    top = rect.top - popRect.height - 4;
  }

  popover.style.top = `${Math.max(8, top)}px`;
  popover.style.left = `${Math.max(8, left)}px`;

  const onDocClick = (e) => {
    if (!popover.contains(e.target) && !triggerEl.contains(e.target)) {
      close();
    }
  };

  const onKeyDown = (e) => {
    if (e.key === "Escape") {
      close();
    }
  };

  setTimeout(() => {
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKeyDown);
  }, 10);

  popoverCleanup = () => {
    document.removeEventListener("click", onDocClick);
    document.removeEventListener("keydown", onKeyDown);
  };
}

function showMobileSheet(triggerEl, items, onSelect, title) {
  let sheet = document.getElementById("actionSheetDialog");
  if (!sheet) {
    sheet = document.createElement("dialog");
    sheet.id = "actionSheetDialog";
    sheet.className = "sheet";
    document.body.appendChild(sheet);
  }

  currentSheet = sheet;
  const close = () => {
    closeDialog(sheet);
    triggerEl?.focus?.();
  };

  sheet.innerHTML = `
    <div class="sheet-shell">
      <div class="sheet-grip"></div>
      <div class="sheet-title">${escapeHtml(title)}</div>
      <div class="sheet-body" id="sheetBodyRoot"></div>
      <div class="sheet-foot" style="padding: 12px var(--gutter);">
        <button class="btn btn-secondary btn-block" id="sheetCancelBtn">取消</button>
      </div>
    </div>
  `;

  const bodyRoot = sheet.querySelector("#sheetBodyRoot");
  bodyRoot.appendChild(renderMenuItems(items, onSelect, close));

  sheet.querySelector("#sheetCancelBtn").onclick = close;

  openDialog(sheet, {
    onClose: () => {
      triggerEl?.focus?.();
      currentSheet = null;
    },
  });
}
