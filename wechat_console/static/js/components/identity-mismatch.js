/* Identity Mismatch Blocking Dialog (C6).
 *
 * When the backend reports identity_binding_state = "mismatch" the slot has
 * logged in with a different WeChat identity than the one bound.  Sync and
 * send are hard-blocked by Core; this dialog is the only way forward:
 * re-login the original WeChat, or explicitly confirm the identity switch
 * via POST /api/runtime/accounts/{id}/confirm-switch.
 */

import { api } from "../api.js";
import { escapeHtml, escapeAttr } from "../format.js";
import { icon } from "../icons.js";
import { openDialog, closeDialog } from "./dialog.js";
import { toast } from "./toast.js";
import { startLogin } from "./login-flow.js";

let dialogEl = null;

function ensureDialog() {
  if (dialogEl && document.body.contains(dialogEl)) return dialogEl;
  let el = document.getElementById("identityMismatchDialog");
  if (!el) {
    el = document.createElement("dialog");
    el.id = "identityMismatchDialog";
    el.className = "modal identity-mismatch-dialog";
    document.body.appendChild(el);
  }
  dialogEl = el;
  return el;
}

/**
 * Show the blocking identity-conflict dialog for one account.
 * @param {ReturnType<import("../account-view-model.js").accountViewModel>} vm
 * @param {object} [options]
 * @param {() => void} [options.onResolved] - called after a confirmed switch.
 */
export function showIdentityMismatchDialog(vm, options = {}) {
  const dialog = ensureDialog();
  const info = vm.mismatchInfo || {};
  const boundName = info.boundNickname || "原微信";
  const boundWxid = info.boundWxid || "";
  const observedWxid = info.observedWxid || "当前登录的微信";

  dialog.innerHTML = `
    <div class="modal-shell">
      <div class="modal-head">
        <div class="modal-head-text">
          <div class="modal-title">${escapeHtml(vm.displayName || vm.name || "微信")} · 检测到登录了另一个微信</div>
          <p class="modal-subtitle">这个运行槽位当前登录的微信身份，与原来绑定的微信不一致。</p>
        </div>
        <button class="btn btn-icon" id="mismatchCloseBtn" aria-label="关闭身份冲突提示">
          ${icon("close")}
        </button>
      </div>
      <div class="modal-body">
        <div class="identity-conflict">
          <div class="identity-conflict-row">
            <div class="identity-conflict-label">原绑定</div>
            <div class="identity-conflict-value">
              <strong>${escapeHtml(boundName)}</strong>
              ${boundWxid ? `<span class="mono caption">${escapeHtml(boundWxid)}</span>` : ""}
            </div>
          </div>
          <div class="identity-conflict-arrow">${icon("arrowLeft")}</div>
          <div class="identity-conflict-row">
            <div class="identity-conflict-label">当前检测</div>
            <div class="identity-conflict-value">
              <strong class="identity-conflict-new">${escapeHtml(observedWxid)}</strong>
            </div>
          </div>
        </div>
        <div class="banner" data-tone="bad">
          <div class="banner-icon">${icon("alertTriangle")}</div>
          <div class="banner-body">
            <div class="banner-title">消息同步和发送已暂停</div>
            <div class="banner-text">为避免两个微信的历史消息混在一起，WeChat Hub 已暂停这个槽位的消息同步与发送，直到你确认如何处理。</div>
          </div>
        </div>
        <p class="caption identity-conflict-note">重新登录原微信会恢复原来的绑定；确认切换则会归档原微信的历史消息，改用当前微信（历史数据不会合并）。</p>
      </div>
      <div class="modal-foot">
        <button class="btn btn-secondary" id="mismatchReloginBtn">重新登录原微信</button>
        <div class="spacer"></div>
        <button class="btn btn-danger" id="mismatchConfirmBtn">确认切换到当前微信</button>
      </div>
    </div>
  `;

  const closeBtn = dialog.querySelector("#mismatchCloseBtn");
  if (closeBtn) closeBtn.onclick = () => closeDialog(dialog);

  const reloginBtn = dialog.querySelector("#mismatchReloginBtn");
  if (reloginBtn) {
    reloginBtn.onclick = () => {
      closeDialog(dialog);
      startLogin(vm.accountId, vm.displayName || vm.name, {
        onSuccess: typeof options.onResolved === "function" ? options.onResolved : undefined,
      });
    };
  }

  const confirmBtn = dialog.querySelector("#mismatchConfirmBtn");
  if (confirmBtn) {
    confirmBtn.onclick = async () => {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "正在确认切换…";
      try {
        const result = await api.confirmSwitch(vm.accountId, {
          observed_wechat_user_id: info.observedWxid || "",
        });
        toast({
          title: "已切换到当前微信",
          text:
            result && result.wechat_user_id
              ? `当前身份：${result.wechat_user_id}`
              : "消息同步和发送已恢复",
          tone: "good",
        });
        closeDialog(dialog);
        if (typeof options.onResolved === "function") options.onResolved();
      } catch (err) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "确认切换到当前微信";
        toast({ title: "切换失败", text: err.message, tone: "bad" });
      }
    };
  }

  openDialog(dialog, { preventCancel: true });
}

window.__identityMismatchModule = { showIdentityMismatchDialog };
