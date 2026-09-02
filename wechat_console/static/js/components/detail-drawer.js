/* Account Advanced Information Drawer.
 *
 * Displays technical details in a slide-out drawer on the right.
 */

import { escapeHtml } from "../format.js";
import { icon } from "../icons.js";
import { openDialog, closeDialog } from "./dialog.js";

let drawerEl = null;

function ensureDrawer() {
  if (drawerEl && document.body.contains(drawerEl)) return drawerEl;
  let el = document.getElementById("accountDetailDrawer");
  if (!el) {
    el = document.createElement("dialog");
    el.id = "accountDetailDrawer";
    el.className = "drawer";
    document.body.appendChild(el);
  }
  drawerEl = el;
  return el;
}

/**
 * Open the account technical details drawer.
 * @param {ReturnType<import("../account-view-model.js").accountViewModel>["advanced"]} advanced
 */
export function showAccountDrawer(advanced) {
  const drawer = ensureDrawer();

  drawer.innerHTML = `
    <div class="drawer-shell">
      <div class="modal-head">
        <div class="modal-head-text">
          <div class="modal-title">${escapeHtml(advanced.name || "微信详情")}</div>
          <p class="modal-subtitle">账号高级运行信息与系统诊断。</p>
        </div>
        <button class="btn btn-icon" id="drawerCloseBtn" aria-label="关闭详情">
          ${icon("close")}
        </button>
      </div>
      <div class="drawer-body">
        <dl class="kv">
          <div><dt>account_id</dt><dd class="mono">${escapeHtml(advanced.accountId)}</dd></div>
          <div><dt>runtime_provider</dt><dd>${escapeHtml(advanced.runtimeProvider)} (${escapeHtml(advanced.providerLabel)})</dd></div>
          <div><dt>Core 状态</dt><dd>${escapeHtml(advanced.coreState)}</dd></div>
          <div><dt>Runtime 状态</dt><dd>${escapeHtml(advanced.runtimeHealth)}</dd></div>
          <div><dt>Agent 服务健康</dt><dd>${escapeHtml(advanced.agentServerHealthy)}</dd></div>
          <div><dt>PID</dt><dd class="mono">${escapeHtml(advanced.pid)}</dd></div>
          <div><dt>UID</dt><dd class="mono">${escapeHtml(advanced.uid)}</dd></div>
          <div><dt>Display</dt><dd class="mono">${escapeHtml(advanced.display)}</dd></div>
          <div><dt>HOME</dt><dd class="mono">${escapeHtml(advanced.home)}</dd></div>
          <div><dt>Docker Image</dt><dd class="mono" style="word-break: break-all;">${escapeHtml(advanced.image)}</dd></div>
          <div><dt>自动启动</dt><dd>${escapeHtml(advanced.autostart)}</dd></div>
          <div><dt>窗口数</dt><dd>${escapeHtml(advanced.windows)}</dd></div>
          <div><dt>发送能力</dt><dd class="mono">${escapeHtml(advanced.senderCapability)}</dd></div>
          <div><dt>最近同步</dt><dd>${escapeHtml(advanced.lastEventAt)}</dd></div>
        </dl>
      </div>
    </div>
  `;

  drawer.querySelector("#drawerCloseBtn").onclick = () => closeDialog(drawer);

  openDialog(drawer);
}
