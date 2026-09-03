/* WeChat Login Dialog Flow & State Machine.
 *
 * Handles runtime login session creation, 3s status polling, snapshot display,
 * desktop gateway opening, and 8 discrete login states.
 */

import { api } from "../api.js";
import { state } from "../state.js";
import { escapeHtml, escapeAttr } from "../format.js";
import { icon } from "../icons.js";
import { openDialog, closeDialog } from "./dialog.js";
import { toast } from "./toast.js";

let loginDialogEl = null;
let pollTimer = null;
let currentAccountId = "";
let currentAccountName = "";
let currentCallbacks = {};

function ensureLoginDialog() {
  if (loginDialogEl && document.body.contains(loginDialogEl)) return loginDialogEl;
  let el = document.getElementById("loginDialog");
  if (!el) {
    el = document.createElement("dialog");
    el.id = "loginDialog";
    el.className = "modal login-dialog";
    el.addEventListener("close", () => {
      stopPolling();
    });
    document.body.appendChild(el);
  } else {
    el.addEventListener("close", () => {
      stopPolling();
    });
  }
  loginDialogEl = el;
  return el;
}

/**
 * Start login flow for an account.
 * @param {string} accountId
 * @param {string} [accountName]
 * @param {object} [callbacks]
 * @param {() => void} [callbacks.onSuccess]
 * @param {() => void} [callbacks.onClose]
 */
export async function startLogin(accountId, accountName = "", callbacks = {}) {
  currentAccountId = accountId;
  currentAccountName = accountName || accountId;
  currentCallbacks = callbacks;

  const dialog = ensureLoginDialog();
  stopPolling();

  // Render initial loading state
  renderStage(dialog, {
    state: "starting",
    snapshot_available: false,
    display_name: currentAccountName,
  });

  openDialog(dialog, {
    onClose: () => {
      stopPolling();
      if (typeof currentCallbacks.onClose === "function") {
        currentCallbacks.onClose();
      }
    },
  });

  // Call startLogin API
  try {
    await api.startLogin(accountId);
  } catch (err) {
    console.warn("startLogin API returned error or was already starting:", err);
  }

  // Poll immediately and start timer only if dialog remains open in an active waiting phase
  const phase = await pollStatus();
  if (
    dialog.open &&
    phase !== "online" &&
    phase !== "error" &&
    phase !== "stopped"
  ) {
    pollTimer = setInterval(pollStatus, 3000);
  }
}

export function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollStatus() {
  if (!currentAccountId || !loginDialogEl || !loginDialogEl.open) {
    stopPolling();
    return "stopped";
  }

  try {
    const status = await api.loginStatus(currentAccountId);
    if (!loginDialogEl.open) {
      stopPolling();
      return "stopped";
    }
    const phase = resolveLoginPhase(status);
    renderStage(loginDialogEl, status);

    if (phase === "online" || phase === "error" || phase === "stopped") {
      stopPolling();
    }
    if (phase === "online") {
      if (typeof currentCallbacks.onSuccess === "function") {
        currentCallbacks.onSuccess();
      }
    }
    return phase;
  } catch (err) {
    stopPolling();
    if (!loginDialogEl.open) return "error";
    renderStage(loginDialogEl, {
      state: "error",
      login_flow_state: "error",
      login_flow_error: err.message || "无法读取登录状态",
    });
    return "error";
  }
}

/**
 * Determine state descriptor from status payload.
 * @param {object} payload
 * @returns {"starting"|"waiting"|"phone_confirm"|"attention"|"online"|"stopped"|"error"|"degraded"}
 */
export function resolveLoginPhase(payload) {
  if (!payload) return "starting";
  if (payload.agent_server_healthy === false) return "degraded";
  if (payload.state === "online") return "online";
  if (payload.state === "stopped") return "stopped";
  if (
    payload.state === "error" ||
    payload.state === "timeout" ||
    payload.login_flow_state === "error" ||
    payload.login_flow_state === "timeout" ||
    Boolean(payload.login_flow_error)
  ) {
    return "error";
  }
  if (payload.state === "attention") return "attention";
  if (payload.login_flow_state === "phone_confirm") return "phone_confirm";
  if (payload.state === "waiting" || payload.snapshot_available) return "waiting";
  return "starting";
}

/**
 * Render the dialog shell and stage content.
 * @param {HTMLDialogElement} dialog
 * @param {object} payload
 */
export function renderStage(dialog, payload) {
  const name = payload.display_name || currentAccountName || "微信";
  const phase = resolveLoginPhase(payload);
  const isMobile = window.innerWidth <= 767;

  let modalTitle = `登录${escapeHtml(name)}`;
  if (phase === "starting") modalTitle = `正在准备${escapeHtml(name)}`;
  if (phase === "online") modalTitle = `${escapeHtml(name)}已连接`;

  let bodyHtml = "";
  let footHtml = "";

  const mobileBannerHtml = isMobile
    ? `<div class="banner" data-tone="info" style="text-align: left; margin-bottom: 12px;">
        <div class="banner-body">
          <div class="banner-title">建议在电脑或平板上打开此页面</div>
          <div class="banner-text">再使用手机微信扫码会更方便。</div>
        </div>
      </div>`
    : "";

  const snapshotUrl = currentAccountId
    ? api.loginSnapshotUrl(currentAccountId)
    : "";

  switch (phase) {
    case "starting": {
      bodyHtml = `
        <div class="login-stage" aria-live="polite">
          ${mobileBannerHtml}
          <p class="login-instruction">正在启动微信并准备登录窗口…</p>
          <div class="login-frame">
            <div class="login-placeholder">
              <div class="progress-indeterminate"></div>
              <span class="caption" style="margin-top: 12px;">正在连接显示服务…</span>
            </div>
          </div>
          <p class="login-note">登录画面只在当前会话中临时显示，不会保存。</p>
        </div>
      `;
      footHtml = `
        <button class="btn btn-ghost btn-sm" id="loginRefreshBtn">刷新画面</button>
        <div class="spacer"></div>
        <button class="btn btn-secondary" id="loginDesktopBtn">打开完整微信</button>
      `;
      break;
    }

    case "waiting": {
      bodyHtml = `
        <div class="login-stage" aria-live="polite">
          ${mobileBannerHtml}
          <p class="login-instruction">请使用手机微信扫描窗口中的二维码。</p>
          <div class="login-frame">
            <img
              id="loginSnapshotImg"
              src="${escapeAttr(snapshotUrl)}"
              alt="微信登录窗口，使用手机微信扫描其中的二维码"
              style="opacity: 0; transition: opacity var(--dur-fast) var(--ease);"
            />
            <div id="loginImgFallback" class="login-placeholder" style="display: none;">
              <div class="progress-indeterminate"></div>
              <span class="caption" style="margin-top: 12px;">微信窗口正在准备，稍后会自动刷新。</span>
            </div>
          </div>
          <p class="login-note">登录画面只在当前会话中临时显示，不会保存。</p>
        </div>
      `;
      footHtml = `
        <button class="btn btn-ghost btn-sm" id="loginRefreshBtn">刷新画面</button>
        <div class="spacer"></div>
        <button class="btn btn-secondary" id="loginDesktopBtn">打开完整微信</button>
      `;
      break;
    }

    case "phone_confirm": {
      bodyHtml = `
        <div class="login-stage" aria-live="polite">
          ${mobileBannerHtml}
          <div class="login-success-mark" style="background: var(--info-soft); border-color: var(--info-border); color: var(--info);">
            ${icon("check", { size: "lg" })}
          </div>
          <div class="login-headline">已扫描二维码</div>
          <p class="login-instruction">请在手机微信中确认登录。</p>
          <div class="progress-indeterminate" style="margin: 16px auto; max-width: 240px;"></div>
          <p class="login-note">该状态来自微信登录流程本身；如果手机上没有出现确认提示，可以刷新画面重试。</p>
        </div>
      `;
      footHtml = `
        <button class="btn btn-ghost btn-sm" id="loginRefreshBtn">刷新画面</button>
        <div class="spacer"></div>
        <button class="btn btn-secondary" id="loginDesktopBtn">打开完整微信</button>
      `;
      break;
    }

    case "attention": {
      bodyHtml = `
        <div class="login-stage" aria-live="polite">
          <div class="banner" style="width: 100%; text-align: left; margin-bottom: 12px;">
            <div class="banner-body">
              <div class="banner-title">微信需要额外确认</div>
              <div class="banner-text">请在下面的微信窗口中完成安全验证。</div>
            </div>
          </div>
          <div class="login-frame">
            <img
              id="loginSnapshotImg"
              src="${escapeAttr(snapshotUrl)}"
              alt="微信安全验证窗口，请在窗口中完成验证"
              style="opacity: 0; transition: opacity var(--dur-fast) var(--ease);"
            />
            <div id="loginImgFallback" class="login-placeholder" style="display: none;">
              <span class="caption">微信窗口正在准备，稍后会自动刷新。</span>
            </div>
          </div>
          <p class="login-note">画面完整显示，不做裁切，避免遮挡确认按钮。</p>
        </div>
      `;
      footHtml = `
        <button class="btn btn-ghost btn-sm" id="loginRefreshBtn">刷新画面</button>
        <div class="spacer"></div>
        <button class="btn btn-primary" id="loginDesktopBtn">打开完整微信</button>
      `;
      break;
    }

    case "online": {
      bodyHtml = `
        <div class="login-stage" style="padding: 24px 0" aria-live="polite">
          <div class="login-success-mark">${icon("check", { size: "lg" })}</div>
          <div class="login-headline">${escapeHtml(name)}已连接</div>
          <p class="login-instruction">消息正在开始同步。以后 WeChat Hub 会自动启动这个微信。</p>
        </div>
      `;
      footHtml = `
        <div class="spacer"></div>
        <button class="btn btn-primary" id="loginDoneBtn">完成</button>
      `;
      break;
    }

    case "stopped": {
      bodyHtml = `
        <div class="login-stage" aria-live="polite">
          <div class="login-headline">这个微信当前已停止</div>
          <p class="login-instruction">启动微信后，会自动准备登录窗口。</p>
        </div>
      `;
      footHtml = `
        <div class="spacer"></div>
        <button class="btn btn-primary" id="loginStartAccountBtn">启动微信</button>
      `;
      break;
    }

    case "error": {
      bodyHtml = `
        <div class="login-stage" aria-live="polite">
          <div class="login-headline">登录窗口暂时不可用</div>
          <p class="login-instruction">微信可能仍在启动，或者登录流程已经超时。</p>
          ${
            payload.login_flow_error
              ? `<p class="caption" style="color: var(--danger-text); margin-top: 8px;">${escapeHtml(
                  payload.login_flow_error
                )}</p>`
              : ""
          }
        </div>
      `;
      footHtml = `
        <button class="btn btn-secondary" id="loginRetryBtn">重新尝试</button>
        <div class="spacer"></div>
        <button class="btn btn-ghost" id="loginDesktopBtn">打开完整微信</button>
      `;
      break;
    }

    case "degraded": {
      bodyHtml = `
        <div class="login-stage" aria-live="polite">
          <div class="login-headline">微信服务异常</div>
          <p class="login-instruction">微信进程仍在运行，但控制服务暂时不可用。</p>
        </div>
      `;
      footHtml = `
        <button class="btn btn-secondary" id="loginRestartBtn">重新启动</button>
      `;
      break;
    }
  }

  dialog.innerHTML = `
    <div class="modal-shell">
      <div class="modal-head">
        <div class="modal-head-text">
          <div class="modal-title">${modalTitle}</div>
        </div>
        <button class="btn btn-icon" id="loginCloseBtn" aria-label="关闭登录窗口">
          ${icon("close")}
        </button>
      </div>
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-foot">${footHtml}</div>
    </div>
  `;

  // Setup image onload / onerror fade
  const img = dialog.querySelector("#loginSnapshotImg");
  const fallback = dialog.querySelector("#loginImgFallback");
  if (img) {
    img.onload = () => {
      img.style.opacity = "1";
      if (fallback) fallback.style.display = "none";
    };
    img.onerror = () => {
      img.style.display = "none";
      if (fallback) fallback.style.display = "flex";
    };
  }

  // Wire buttons
  const closeBtn = dialog.querySelector("#loginCloseBtn");
  if (closeBtn) closeBtn.onclick = () => closeDialog(dialog);

  const doneBtn = dialog.querySelector("#loginDoneBtn");
  if (doneBtn) doneBtn.onclick = () => closeDialog(dialog);

  const refreshBtn = dialog.querySelector("#loginRefreshBtn");
  if (refreshBtn) {
    refreshBtn.onclick = async () => {
      refreshBtn.disabled = true;
      await pollStatus();
      setTimeout(() => {
        if (refreshBtn) refreshBtn.disabled = false;
      }, 1000);
    };
  }

  const retryBtn = dialog.querySelector("#loginRetryBtn");
  if (retryBtn) {
    retryBtn.onclick = () => startLogin(currentAccountId, currentAccountName, currentCallbacks);
  }

  const startAccBtn = dialog.querySelector("#loginStartAccountBtn");
  if (startAccBtn) {
    startAccBtn.onclick = async () => {
      try {
        await api.accountAction(currentAccountId, "start");
        toast({ title: "已发送启动指令", tone: "good" });
        await pollStatus();
      } catch (err) {
        toast({ title: "启动失败", text: err.message, tone: "bad" });
      }
    };
  }

  const restartBtn = dialog.querySelector("#loginRestartBtn");
  if (restartBtn) {
    restartBtn.onclick = async () => {
      try {
        await api.accountAction(currentAccountId, "restart");
        toast({ title: "已发送重启指令", tone: "good" });
        await pollStatus();
      } catch (err) {
        toast({ title: "重启失败", text: err.message, tone: "bad" });
      }
    };
  }

  const desktopBtn = dialog.querySelector("#loginDesktopBtn");
  if (desktopBtn) {
    desktopBtn.onclick = () => openDesktopEntry(currentAccountId);
  }
}

/**
 * Open desktop gateway in a new browser tab.
 * @param {string} accountId
 */
export async function openDesktopEntry(accountId) {
  try {
    let url = "";
    try {
      const info = await api.desktop(accountId);
      const isAgentWechat = info?.runtime_provider === "agent_wechat";
      if (info && info.port && info.path) {
        const port = Number(info.port);
        const path = String(info.path);
        if (port >= 1 && port <= 65535 && path.startsWith("/")) {
          if (info.desktop_provider === "novnc" && info.fallback_reason) {
            toast({
              title: "当前使用备用桌面",
              text: "增强中文输入、剪贴板、文件传输和缩放将在 Selkies 桌面可用后自动恢复。",
              tone: "warn",
            });
          }
          const scheme = info.scheme || window.location.protocol.replace(":", "");
          const host = info.host || window.location.hostname;
          url = `${scheme}://${host}:${port}${path}`;
        }
      }
      if (!url && !isAgentWechat && state.status?.desktop_url) {
        url = state.status.desktop_url.includes("{account_id}")
          ? state.status.desktop_url.replace(/\{account_id\}/g, encodeURIComponent(accountId))
          : state.status.desktop_url;
      }
    } catch (apiErr) {
      const account = (state.runtimeAccounts || []).find((a) => a.account_id === accountId) ||
                      (state.accounts || []).find((a) => a.account_id === accountId);
      const provider = account?.runtime_provider || account?.runtime?.runtime_provider || "legacy";
      if (provider !== "agent_wechat" && state.status?.desktop_url) {
        url = state.status.desktop_url.includes("{account_id}")
          ? state.status.desktop_url.replace(/\{account_id\}/g, encodeURIComponent(accountId))
          : state.status.desktop_url;
      } else {
        throw apiErr;
      }
    }

    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
      return;
    }
    toast({ title: "微信桌面入口尚未就绪，请稍后重试。", tone: "warn" });
  } catch (err) {
    toast({ title: "打开桌面失败", text: err.message, tone: "bad" });
  }
}

window.__loginFlowModule = { resolveLoginPhase, startLogin, stopPolling, openDesktopEntry };
