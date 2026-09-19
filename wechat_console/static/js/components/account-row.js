/* Account Row Component.
 *
 * Renders the primary list row for an account using accountViewModel data.
 * Card hierarchy (C1): real WeChat identity → Hub display_name → status →
 * last sync, with a photo avatar that degrades to a glyph fallback (C2).
 */

import { escapeHtml, escapeAttr } from "../format.js";
import { icon } from "../icons.js";
import { statusMarkup } from "./status.js";

/**
 * Render account row markup.
 * @param {ReturnType<import("../account-view-model.js").accountViewModel>} vm
 * @param {object} [options]
 * @param {boolean} [options.compact=false]
 * @param {boolean} [options.showPill=true]
 * @param {boolean} [options.showHint=true]
 * @param {boolean} [options.showMore=true]
 * @param {boolean} [options.showSync=true]
 * @param {boolean} [options.stack=false]
 * @returns {string}
 */
export function renderAccountRow(
  vm,
  {
    compact = false,
    showPill = true,
    showHint = true,
    showMore = true,
    showSync = true,
    stack = false,
  } = {}
) {
  const pillHtml =
    showPill && vm.providerLabel
      ? `<span class="pill" data-tone="${
          vm.provider === "agent_wechat" ? "brand" : "neutral"
        }">${escapeHtml(vm.providerLabel)}</span>`
      : "";

  // C1 — the Hub display_name is visually distinct from the real WeChat name.
  const hubNameHtml = vm.showHubName
    ? `<span class="hub-name" title="WeChat Hub 备注名">${escapeHtml(
        vm.hubName
      )}</span>`
    : "";

  // P0-3 — a wxid may only be shown as what it is: the internal account id.
  // It must never be presented as if it were the WeChat nickname.
  const internalIdBadgeHtml = vm.isInternalAccountId
    ? `<span class="pill" data-tone="neutral" title="这是微信内部账号 ID，不是微信昵称">内部账号 ID</span>`
    : "";

  // P0-3 — while Core is still reading the WeChat profile, say so instead of
  // showing a fallback that looks like a real name.
  const hydratingHtml = vm.identityHydrating
    ? `<span class="hub-name" data-state="loading">正在读取微信资料…</span>`
    : "";

  const hintHtml =
    showHint && vm.hint ? `<span>${escapeHtml(vm.hint)}</span>` : "";

  const syncHtml =
    showSync && vm.lastSyncText
      ? `<div class="row-sync">${icon("clock", { size: "sm" })}<span>最近同步 ${escapeHtml(
          vm.lastSyncText
        )}</span></div>`
      : "";

  // C2 — photo avatar overlays the glyph fallback; wireAccountAvatars() shows
  // it only after a successful load, so a broken image never breaks layout.
  // Not lazy: the img starts display:none and lazy loading would never fire.
  const avatarPhotoHtml = vm.avatarSrc
    ? `<img class="avatar-photo" src="${escapeAttr(vm.avatarSrc)}" alt="" style="display: none;" />`
    : "";

  const btnClass = `btn btn-${vm.primaryAction.variant || "secondary"}${
    compact ? " btn-sm" : ""
  }`;

  const disabledAttr = vm.primaryAction.disabled ? "disabled" : "";

  const moreBtnHtml = showMore
    ? `<button class="btn btn-icon btn-more" data-action="more" aria-label="${escapeAttr(
        vm.displayName
      )}的更多操作">
        ${icon("more")}
      </button>`
    : "";

  return `
    <div class="row" data-account-id="${escapeAttr(vm.accountId)}" ${
    vm.identityMismatch ? 'data-identity-state="mismatch"' : ""
  } ${stack ? 'data-stack="true"' : ""}>
      <div class="avatar" data-tone="${escapeAttr(vm.tone)}"><span class="avatar-fallback">${escapeHtml(
        vm.initial
      )}</span>${avatarPhotoHtml}</div>
      <div class="row-body">
        <div class="row-title">
          <strong>${escapeHtml(vm.displayName)}</strong>${pillHtml}${internalIdBadgeHtml}
        </div>
        <div class="row-meta">
          ${hubNameHtml}${hydratingHtml}${statusMarkup(vm.tone, vm.statusText)}${hintHtml}
        </div>
        ${syncHtml}
      </div>
      ${showMore ? moreBtnHtml : ""}
      <div class="row-actions">
        <button class="${btnClass}" data-action="${escapeAttr(
          vm.primaryAction.id
        )}" ${disabledAttr}>${escapeHtml(vm.primaryAction.label)}</button>
      </div>
    </div>
  `;
}

/**
 * C2 — show photo avatars only once they have actually loaded; a 404 or any
 * load error leaves the glyph fallback in place (never a broken image).
 * @param {ParentNode} root
 */
export function wireAccountAvatars(root) {
  if (!root || typeof root.querySelectorAll !== "function") return;
  root.querySelectorAll(".avatar-photo").forEach((img) => {
    if (!img.dataset.wired) {
      img.dataset.wired = "1";
      img.addEventListener("load", () => {
        img.style.display = "block";
      });
      img.addEventListener("error", () => {
        img.style.display = "none";
      });
    }
    if (img.complete && img.naturalWidth > 0) {
      img.style.display = "block";
    }
  });
}
