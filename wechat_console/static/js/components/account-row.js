/* Account Row Component.
 *
 * Renders the primary list row for an account using accountViewModel data.
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
    stack = false,
  } = {}
) {
  const pillHtml =
    showPill && vm.providerLabel
      ? `<span class="pill" data-tone="${
          vm.provider === "agent_wechat" ? "brand" : "neutral"
        }">${escapeHtml(vm.providerLabel)}</span>`
      : "";

  const hintHtml =
    showHint && vm.hint ? `<span>${escapeHtml(vm.hint)}</span>` : "";

  const btnClass = `btn btn-${vm.primaryAction.variant || "secondary"}${
    compact ? " btn-sm" : ""
  }`;

  const disabledAttr = vm.primaryAction.disabled ? "disabled" : "";

  const moreBtnHtml = showMore
    ? `<button class="btn btn-icon btn-more" data-action="more" aria-label="${escapeAttr(
        vm.name
      )}的更多操作">
        ${icon("more")}
      </button>`
    : "";

  return `
    <div class="row" data-account-id="${escapeAttr(vm.accountId)}" ${
    stack ? 'data-stack="true"' : ""
  }>
      <div class="avatar" data-tone="${escapeAttr(vm.tone)}">${escapeHtml(
        vm.initial
      )}</div>
      <div class="row-body">
        <div class="row-title">
          <strong>${escapeHtml(vm.name)}</strong>${pillHtml}
        </div>
        <div class="row-meta">
          ${statusMarkup(vm.tone, vm.statusText)}${hintHtml}
        </div>
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
