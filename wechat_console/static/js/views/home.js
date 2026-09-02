/* Home View.
 *
 * Sections:
 * 1. 需要处理 (Needs Attention - conditional)
 * 2. 我的微信 (My WeChat Accounts)
 * 3. 最近消息 (Recent Messages)
 * 4. Empty & Core offline states
 */

import { state } from "../state.js";
import { navigate } from "../router.js";
import { accountViewModel } from "../account-view-model.js";
import { renderAccountRow } from "../components/account-row.js";
import { escapeHtml, fmtWhen, initial } from "../format.js";
import { icon } from "../icons.js";
import { showMenu } from "../components/menu.js";
import { showAccountDrawer } from "../components/detail-drawer.js";
import { startLogin } from "../components/login-flow.js";
import { confirmAction } from "../components/confirm.js";
import { toast } from "../components/toast.js";
import { api } from "../api.js";

/**
 * Render Home view.
 * @param {HTMLElement} container
 * @param {() => Promise<void>} reloadData
 */
export function renderHomeView(container, reloadData) {
  const coreOk = state.coreOk !== false && state.status?.core?.ok !== false;
  const runtimeAccounts = state.runtimeAccounts || [];
  const coreAccounts = state.accounts || [];

  // Handle Core offline state
  if (!coreOk) {
    container.innerHTML = `
      <div class="page-inner">
        <div class="page-head">
          <div>
            <div class="page-title">首页</div>
            <p class="page-subtitle">这台 NAS 上的微信现在怎么样。</p>
          </div>
        </div>
        <div class="surface">
          <div class="empty">
            <div class="empty-icon">${icon("alertCircle")}</div>
            <div class="empty-title">WeChat Hub 暂时无法连接</div>
            <p class="empty-text">账号和消息可能暂时无法更新。已经同步过的消息和收藏仍然可以查看。</p>
            <button class="btn btn-primary" id="homeReconnectBtn">重新连接</button>
          </div>
        </div>
      </div>
    `;
    container.querySelector("#homeReconnectBtn").onclick = reloadData;
    return;
  }

  // Build Account View Models
  const allIds = Array.from(
    new Set([
      ...runtimeAccounts.map((a) => a.account_id),
      ...coreAccounts.map((a) => a.account_id),
    ])
  );

  const vms = allIds.map((id) => {
    const ra = runtimeAccounts.find((a) => a.account_id === id);
    const ca = coreAccounts.find((a) => a.account_id === id);
    return accountViewModel(ra, ca, {
      runtimeManagement: state.runtimeManagement,
      coreOk,
    });
  });

  // Check if completely empty
  if (vms.length === 0) {
    container.innerHTML = `
      <div class="page-inner">
        <div class="page-head">
          <div>
            <div class="page-title">首页</div>
            <p class="page-subtitle">这台 NAS 上的微信现在怎么样。</p>
          </div>
        </div>
        <div class="surface">
          <div class="empty">
            <div class="empty-icon">${icon("wechat")}</div>
            <div class="empty-title">还没有添加微信</div>
            <p class="empty-text">添加第一个微信后，可以在这里扫码登录并管理消息。</p>
            <button class="btn btn-primary" id="homeAddFirstAccountBtn">
              ${icon("plus", { size: "sm" })}添加微信
            </button>
          </div>
        </div>
      </div>
    `;
    container.querySelector("#homeAddFirstAccountBtn").onclick = () => {
      navigate("accounts");
      window.dispatchEvent(new CustomEvent("wechat-hub:open-add-wizard"));
    };
    return;
  }

  // 1. Needs attention accounts
  const attentionVms = vms.filter((vm) => {
    if (vm.tone === "bad") return true;
    if (vm.tone === "warn") return true;
    return false;
  });

  let attentionHtml = "";
  if (attentionVms.length > 0) {
    const topAtt = attentionVms[0];
    const bannerTitle =
      topAtt.tone === "bad"
        ? `${escapeHtml(topAtt.name)}服务异常`
        : `${escapeHtml(topAtt.name)}需要登录`;
    const bannerText = topAtt.hint || "点击操作按钮处理此微信。";
    const actionLabel = topAtt.primaryAction.label || "去处理";

    attentionHtml = `
      <div class="section">
        <div class="section-head">
          <div class="section-head-text"><h2 class="section-title">需要处理</h2></div>
        </div>
        <div class="banner">
          <div class="banner-icon">${icon("alertTriangle")}</div>
          <div class="banner-body">
            <div class="banner-title">${bannerTitle}</div>
            <div class="banner-text">${escapeHtml(bannerText)}</div>
          </div>
          <div class="banner-actions">
            <button class="btn btn-primary btn-sm" id="homeTopAttentionBtn">${escapeHtml(
              actionLabel
            )}</button>
          </div>
        </div>
      </div>
    `;
  }

  // 2. My accounts rows
  const accountRowsHtml = vms
    .map((vm) =>
      renderAccountRow(vm, {
        compact: true,
        showPill: false,
        showMore: false,
        stack: false,
      })
    )
    .join("");

  // 3. Recent messages
  const recentMessages = (state.messages || []).slice(0, 5);
  let recentMessagesHtml = "";
  if (recentMessages.length > 0) {
    const msgRows = recentMessages
      .map((msg) => {
        const chatName = msg.chat_name || msg.sender_name || msg.chat_id || "微信会话";
        const acc = vms.find((v) => v.accountId === msg.account_id);
        const accLabel = acc ? acc.name : msg.account_id;
        const initialChar = initial(chatName, "微");
        const when = fmtWhen(msg.timestamp || msg.created_at || msg.occurred_at);

        let snippet = msg.text || "";
        if (msg.type === "image") snippet = `[图片] ${msg.filename || "图片"}`;
        else if (msg.type === "file") snippet = `[文件] ${msg.filename || "文件"}`;
        else if (msg.type === "video") snippet = "[视频]";
        else if (msg.type === "audio") snippet = "[语音]";

        return `
          <div class="row" style="cursor: pointer;" data-jump-msg="${escapeHtml(
            msg.account_id
          )}" data-jump-chat="${escapeHtml(msg.chat_id)}">
            <div class="avatar avatar-sm">${escapeHtml(initialChar)}</div>
            <div class="row-body">
              <div class="row-title">
                <strong>${escapeHtml(chatName)}</strong>
                <span class="caption">${escapeHtml(accLabel)}</span>
              </div>
              <div class="row-meta">
                <span class="truncate">${escapeHtml(snippet)}</span>
              </div>
            </div>
            <span class="caption">${escapeHtml(when)}</span>
          </div>
        `;
      })
      .join("");

    recentMessagesHtml = `
      <div class="section">
        <div class="section-head">
          <div class="section-head-text"><h2 class="section-title">最近消息</h2></div>
          <button class="btn btn-ghost btn-sm" id="homeJumpMessagesBtn">
            打开消息${icon("chevronRight", { size: "sm" })}
          </button>
        </div>
        <div class="surface surface-flush">
          <div class="rows">${msgRows}</div>
        </div>
      </div>
    `;
  } else {
    recentMessagesHtml = `
      <div class="section">
        <div class="section-head">
          <div class="section-head-text"><h2 class="section-title">最近消息</h2></div>
          <button class="btn btn-ghost btn-sm" id="homeJumpMessagesBtn">
            打开消息${icon("chevronRight", { size: "sm" })}
          </button>
        </div>
        <div class="surface" style="padding: 24px; text-align: center; color: var(--text-secondary);">
          <span>尚未同步到消息。</span>
        </div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="page-inner">
      <div class="page-head">
        <div>
          <div class="page-title">首页</div>
          <p class="page-subtitle">这台 NAS 上的微信现在怎么样。</p>
        </div>
      </div>

      ${attentionHtml}

      <div class="section">
        <div class="section-head">
          <div class="section-head-text"><h2 class="section-title">我的微信</h2></div>
          <button class="btn btn-ghost btn-sm" id="homeManageAccountsBtn">
            全部管理${icon("chevronRight", { size: "sm" })}
          </button>
        </div>
        <div class="surface surface-flush">
          <div class="rows">${accountRowsHtml}</div>
        </div>
      </div>

      ${recentMessagesHtml}
    </div>
  `;

  // Wire navigation buttons
  const manageBtn = container.querySelector("#homeManageAccountsBtn");
  if (manageBtn) manageBtn.onclick = () => navigate("accounts");

  const jumpMsgBtn = container.querySelector("#homeJumpMessagesBtn");
  if (jumpMsgBtn) jumpMsgBtn.onclick = () => navigate("messages");

  const topAttBtn = container.querySelector("#homeTopAttentionBtn");
  if (topAttBtn && attentionVms.length > 0) {
    const topAtt = attentionVms[0];
    topAttBtn.onclick = () => handleAction(topAtt.primaryAction.id, topAtt, reloadData);
  }

  // Wire row actions
  container.querySelectorAll(".row[data-account-id]").forEach((row) => {
    const accId = row.dataset.accountId;
    const vm = vms.find((v) => v.accountId === accId);
    if (!vm) return;

    row.querySelectorAll("button[data-action]").forEach((btn) => {
      const act = btn.dataset.action;
      btn.onclick = (e) => {
        e.stopPropagation();
        if (act === "more") {
          showMenu(btn, vm.menu, (chosen) => handleAction(chosen, vm, reloadData));
        } else {
          handleAction(act, vm, reloadData);
        }
      };
    });
  });

  // Wire message row clicks
  container.querySelectorAll(".row[data-jump-msg]").forEach((row) => {
    row.onclick = () => {
      const accId = row.dataset.jumpMsg;
      const chatId = row.dataset.jumpChat;
      state.activeAccountId = accId;
      state.selectedChatId = chatId;
      navigate("messages");
    };
  });
}

async function handleAction(action, vm, reloadData) {
  switch (action) {
    case "open":
    case "messages": {
      state.activeAccountId = vm.accountId;
      navigate("messages");
      break;
    }
    case "login":
    case "relogin": {
      startLogin(vm.accountId, vm.name, { onSuccess: reloadData });
      break;
    }
    case "start":
    case "restart":
    case "stop": {
      try {
        await api.accountAction(vm.accountId, action);
        toast({ title: `已发送${action === "start" ? "启动" : action === "stop" ? "停止" : "重启"}指令`, tone: "good" });
        await reloadData();
      } catch (err) {
        toast({ title: "操作失败", text: err.message, tone: "bad" });
      }
      break;
    }
    case "advanced": {
      showAccountDrawer(vm.advanced);
      break;
    }
    case "remove": {
      if (vm.isLegacyDefault) {
        toast({ title: "兼容模式默认微信不可移除", tone: "warn" });
        return;
      }
      const confirmed = await confirmAction({
        title: `移除微信「${vm.name}」？`,
        text:
          vm.provider === "agent_wechat"
            ? "上游容器会删除，但账号数据会保留，重新添加同一账号可继续使用。"
            : "微信进程会停止，但登录数据会保留。",
        confirmLabel: "确认移除",
        tone: "danger",
      });
      if (!confirmed) return;
      try {
        await api.removeAccount(vm.accountId);
        toast({ title: `已移除微信「${vm.name}」`, tone: "good" });
        await reloadData();
      } catch (err) {
        toast({ title: "移除失败", text: err.message, tone: "bad" });
      }
      break;
    }
  }
}
