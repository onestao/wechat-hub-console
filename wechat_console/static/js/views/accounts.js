/* Accounts Management View & Add Wizard.
 *
 * Handles account listing, lifecycle operations, inline removing animation,
 * and the Add Account modal wizard.
 */

import { state } from "../state.js";
import { api } from "../api.js";
import { accountViewModel } from "../account-view-model.js";
import { renderAccountRow } from "../components/account-row.js";
import { showMenu } from "../components/menu.js";
import { showAccountDrawer } from "../components/detail-drawer.js";
import { startLogin, openDesktopEntry } from "../components/login-flow.js";
import { confirmAction } from "../components/confirm.js";
import { toast } from "../components/toast.js";
import { openDialog, closeDialog } from "../components/dialog.js";
import { escapeHtml, escapeAttr } from "../format.js";
import { icon } from "../icons.js";

let addWizardEl = null;

/**
 * Render Accounts View.
 * @param {HTMLElement} container
 * @param {() => Promise<void>} reloadData
 */
export function renderAccountsView(container, reloadData) {
  const coreOk = state.coreOk !== false && state.status?.core?.ok !== false;
  const runtimeAccounts = state.runtimeAccounts || [];
  const coreAccounts = state.accounts || [];
  const management = state.runtimeManagement || {};
  const runtimeAvailable = Boolean(management.ok || (management.configured && management.available));

  const allIds = Array.from(
    new Set([
      ...runtimeAccounts.map((a) => a.account_id),
      ...coreAccounts.map((a) => a.account_id),
    ])
  );

  const vms = allIds.map((id) => {
    const ra = runtimeAccounts.find((a) => a.account_id === id);
    const ca = coreAccounts.find((a) => a.account_id === id);
    return accountViewModel(ra, ca, { runtimeManagement: management, coreOk });
  });

  let runtimeBannerHtml = "";
  if (management.supported === false) {
    runtimeBannerHtml = `
      <div class="banner" data-tone="warn" style="margin-bottom: var(--sp-3);">
        <div class="banner-body">
          <div class="banner-title">微信管理暂时不可用</div>
          <div class="banner-text">已存在的消息仍然可以查看。</div>
        </div>
      </div>
    `;
  } else if (!runtimeAvailable && coreOk) {
    runtimeBannerHtml = `
      <div class="banner" data-tone="bad" style="margin-bottom: var(--sp-3);">
        <div class="banner-body">
          <div class="banner-title">微信管理服务离线</div>
          <div class="banner-text">${escapeHtml(management.error || "微信控制服务暂时不可用。")}</div>
        </div>
        <div class="banner-actions">
          <button class="btn btn-secondary btn-sm" id="accountsRetryRuntimeBtn">重试</button>
        </div>
      </div>
    `;
  }

  if (vms.length === 0) {
    container.innerHTML = `
      <div class="page-inner">
        <div class="page-head">
          <div>
            <div class="page-title">微信</div>
            <p class="page-subtitle">管理运行在这台 NAS 上的微信账号。</p>
          </div>
        </div>
        ${runtimeBannerHtml}
        <div class="surface">
          <div class="empty">
            <div class="empty-icon">${icon("wechat")}</div>
            <div class="empty-title">还没有添加微信</div>
            <p class="empty-text">添加第一个微信后，可以在这里扫码登录并管理消息。</p>
            <button class="btn btn-primary" id="accountsAddBtnEmpty" ${!runtimeAvailable ? "disabled" : ""}>
              ${icon("plus", { size: "sm" })}添加微信
            </button>
          </div>
        </div>
      </div>
    `;
    const addBtnEmpty = container.querySelector("#accountsAddBtnEmpty");
    if (addBtnEmpty) addBtnEmpty.onclick = () => openAddWizard(vms, reloadData);
    return;
  }

  const rowsHtml = vms
    .map((vm) =>
      renderAccountRow(vm, {
        compact: false,
        showPill: true,
        showMore: true,
        stack: true,
      })
    )
    .join("");

  container.innerHTML = `
    <div class="page-inner">
      <div class="page-head">
        <div>
          <div class="page-title">微信</div>
          <p class="page-subtitle">管理运行在这台 NAS 上的微信账号。</p>
        </div>
        <div class="page-head-actions">
          <button class="btn btn-ghost btn-sm" id="accountsRefreshBtn" aria-label="刷新状态" ${!runtimeAvailable ? "disabled" : ""}>
            ${icon("refresh", { size: "sm" })}刷新
          </button>
          <button class="btn btn-primary" id="accountsAddBtn" ${!runtimeAvailable ? "disabled" : ""}>
            ${icon("plus", { size: "sm" })}添加微信
          </button>
        </div>
      </div>

      ${runtimeBannerHtml}

      <div class="surface surface-flush">
        <div class="rows" id="accountsListRoot">${rowsHtml}</div>
      </div>
    </div>
  `;

  const refreshBtn = container.querySelector("#accountsRefreshBtn");
  if (refreshBtn) {
    refreshBtn.onclick = async () => {
      if (!runtimeAvailable) return;
      refreshBtn.disabled = true;
      await reloadData();
      setTimeout(() => {
        if (refreshBtn) refreshBtn.disabled = !runtimeAvailable;
      }, 1000);
    };
  }

  const retryBtn = container.querySelector("#accountsRetryRuntimeBtn");
  if (retryBtn) retryBtn.onclick = reloadData;

  const addBtn = container.querySelector("#accountsAddBtn");
  if (addBtn) addBtn.onclick = () => openAddWizard(vms, reloadData);

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
          showMenu(btn, vm.menu, (chosen) =>
            handleAccountAction(chosen, vm, row, reloadData)
          );
        } else {
          handleAccountAction(act, vm, row, reloadData);
        }
      };
    });
  });
}

async function handleAccountAction(action, vm, rowEl, reloadData) {
  switch (action) {
    case "open": {
      openDesktopEntry(vm.accountId);
      break;
    }
    case "messages": {
      state.activeAccountId = vm.accountId;
      window.location.hash = "#/messages";
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
        toast({
          title: `已发送${action === "start" ? "启动" : action === "stop" ? "停止" : "重启"}指令`,
          tone: "good",
        });
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
        if (rowEl) {
          rowEl.classList.add("is-removing");
          await new Promise((r) => setTimeout(r, 220));
        }
        await api.removeAccount(vm.accountId);
        toast({ title: `已移除微信「${vm.name}」`, tone: "good" });
        await reloadData();
      } catch (err) {
        if (rowEl) rowEl.classList.remove("is-removing");
        toast({ title: "移除失败", text: err.message, tone: "bad" });
      }
      break;
    }
  }
}

function generateSlug(name, existingIds = []) {
  const sanitized = String(name || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "")
    .replace(/^[^a-z0-9]+/g, "");

  let base = sanitized;
  if (!base) {
    let n = 1;
    while (existingIds.includes(`wechat-${n}`)) {
      n++;
    }
    return `wechat-${n}`;
  }

  base = base.slice(0, 64);
  let candidate = base;
  let counter = 1;

  while (existingIds.includes(candidate)) {
    counter++;
    const suffix = `-${counter}`;
    const maxBaseLen = 64 - suffix.length;
    candidate = `${base.slice(0, maxBaseLen)}${suffix}`;
  }

  return candidate;
}

export function openAddWizard(existingVms = [], reloadData) {
  const existingIds = existingVms.map((v) => v.accountId);
  let wizard = addWizardEl;
  if (!wizard || !document.body.contains(wizard)) {
    wizard = document.getElementById("addAccountDialog");
    if (!wizard) {
      wizard = document.createElement("dialog");
      wizard.id = "addAccountDialog";
      wizard.className = "modal";
      document.body.appendChild(wizard);
    }
    addWizardEl = wizard;
  }

  const renderWizardBody = (formData = {}, errors = {}) => {
    const nameVal = formData.displayName ?? "";
    const idVal = formData.accountId ?? "";
    const providerVal = formData.runtimeProvider ?? "agent_wechat";
    const displayVal = formData.display ?? "";
    const autostartVal = formData.autostart ?? true;
    const startVal = formData.start ?? true;

    wizard.innerHTML = `
      <div class="modal-shell">
        <div class="modal-head">
          <div class="modal-head-text">
            <div class="modal-title">添加微信</div>
            <p class="modal-subtitle">起个名字就可以，其余设置由 WeChat Hub 自动准备。</p>
          </div>
          <button class="btn btn-icon" id="addWizardCloseBtn" aria-label="关闭">
            ${icon("close")}
          </button>
        </div>
        <form id="addAccountForm" class="modal-body" novalidate>
          <div class="field ${errors.name ? "has-error" : ""}">
            <label class="label" for="addAccName">给这个微信起一个名字</label>
            <input class="input" id="addAccName" name="displayName" value="${escapeAttr(
              nameVal
            )}" placeholder="例如：工作微信、个人微信" ${
      errors.name ? 'aria-describedby="addAccNameError"' : ""
    } required />
            <p class="field-hint">只用于在 WeChat Hub 里区分不同微信，随时可以改。</p>
            ${
              errors.name
                ? `<p class="field-error" id="addAccNameError">${escapeHtml(
                    errors.name
                  )}</p>`
                : ""
            }
          </div>

          <div class="banner" data-tone="info" style="margin-bottom: var(--sp-4);">
            <div class="banner-icon">${icon("info")}</div>
            <div class="banner-body">
              <div class="banner-title">创建后会自动启动并进入扫码登录</div>
              <div class="banner-text">默认使用推荐模式（Beta），每个微信独立运行，支持更多操作。</div>
            </div>
          </div>

          <details class="disclosure" ${errors.id ? "open" : ""}>
            <summary>高级选项</summary>
            <div class="disclosure-body" style="display: flex; flex-direction: column; gap: 14px; margin-top: 12px;">
              <div class="field ${errors.id ? "has-error" : ""}">
                <label class="label" for="addAccId">账号 ID</label>
                <input class="input mono" id="addAccId" name="accountId" value="${escapeAttr(
                  idVal
                )}" placeholder="留空自动生成，例如 wechat-2" ${
      errors.id ? 'aria-describedby="addAccIdError"' : ""
    } />
                <p class="field-hint">Core / Telegram 集成使用的稳定标识，创建后不可更改。</p>
                ${
                  errors.id
                    ? `<p class="field-error" id="addAccIdError">${escapeHtml(
                        errors.id
                      )}</p>`
                    : ""
                }
              </div>

              <div class="field">
                <label class="label" for="addAccProvider">运行模式</label>
                <select class="select" id="addAccProvider" name="runtimeProvider">
                  <option value="agent_wechat" ${
                    providerVal === "agent_wechat" ? "selected" : ""
                  }>推荐模式（Beta）— AgentWechat</option>
                  <option value="legacy" ${
                    providerVal === "legacy" ? "selected" : ""
                  }>兼容模式 — Legacy</option>
                </select>
                <p class="field-hint">推荐模式每个微信独立运行；兼容模式用于继续使用已有旧版微信数据。</p>
              </div>

              <div class="field" id="addAccDisplayField" style="${providerVal === "legacy" ? "" : "display: none;"}">
                <label class="label" for="addAccDisplay">Legacy Display</label>
                <input class="input mono" id="addAccDisplay" name="display" value="${escapeAttr(
                  displayVal
                )}" placeholder="例如：:1；留空则使用 Runtime 默认 Display。" />
                <p class="field-hint">例如：:1；留空则使用 Runtime 默认 Display。</p>
              </div>

              <label class="check">
                <input type="checkbox" id="addAccAutostart" name="autostart" ${
                  autostartVal ? "checked" : ""
                } />
                <span class="check-text">
                  <strong>自动启动</strong>
                  <span>WeChat Hub 启动时一起启动这个微信。</span>
                </span>
              </label>

              <label class="check">
                <input type="checkbox" id="addAccStart" name="start" ${
                  startVal ? "checked" : ""
                } />
                <span class="check-text">
                  <strong>创建后立即启动并登录</strong>
                  <span>关闭后需要稍后手动启动。</span>
                </span>
              </label>
            </div>
          </details>

          ${
            errors.global
              ? `<div class="field-error" style="margin-top: 8px;">${escapeHtml(
                  errors.global
                )}</div>`
              : ""
          }
        </form>
        <div class="modal-foot">
          <button class="btn btn-ghost" id="addWizardCancelBtn" type="button">取消</button>
          <button class="btn btn-primary" id="addWizardSubmitBtn" type="button">创建并登录</button>
        </div>
      </div>
    `;

    wizard.querySelector("#addWizardCloseBtn").onclick = () =>
      closeDialog(wizard);
    wizard.querySelector("#addWizardCancelBtn").onclick = () =>
      closeDialog(wizard);

    const form = wizard.querySelector("#addAccountForm");
    const submitBtn = wizard.querySelector("#addWizardSubmitBtn");
    const startCheck = wizard.querySelector("#addAccStart");
    const providerSelect = wizard.querySelector("#addAccProvider");
    const displayField = wizard.querySelector("#addAccDisplayField");

    if (providerSelect && displayField) {
      providerSelect.onchange = () => {
        displayField.style.display = providerSelect.value === "legacy" ? "" : "none";
      };
    }

    if (startCheck && submitBtn) {
      startCheck.onchange = () => {
        submitBtn.textContent = startCheck.checked ? "创建并登录" : "创建微信";
      };
    }

    const handleSubmit = async () => {
      const name = (form.querySelector("#addAccName").value || "").trim();
      let customId = (form.querySelector("#addAccId").value || "").trim();
      const provider = form.querySelector("#addAccProvider").value;
      const display = (form.querySelector("#addAccDisplay")?.value || "").trim();
      const autostart = form.querySelector("#addAccAutostart").checked;
      const start = form.querySelector("#addAccStart").checked;

      const newErrors = {};
      if (!name) {
        newErrors.name = "请填写微信名称";
      }

      if (!customId) {
        customId = generateSlug(name, existingIds);
      }

      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(customId)) {
        newErrors.id =
          "账号 ID 必须以字母或数字开头，且只能包含字母、数字、下划线、短横线和点（最多64字符）";
      } else if (existingIds.includes(customId)) {
        newErrors.id = `账号 ID「${customId}」已存在，请换一个`;
      }

      if (Object.keys(newErrors).length > 0) {
        renderWizardBody(
          {
            displayName: name,
            accountId: customId,
            runtimeProvider: provider,
            display,
            autostart,
            start,
          },
          newErrors
        );
        return;
      }

      // Submit to backend
      submitBtn.disabled = true;
      submitBtn.textContent = "正在创建…";

      try {
        const payload = {
          account_id: customId,
          display_name: name,
          runtime_provider: provider,
          autostart,
          start,
        };
        if (provider === "legacy" && display) {
          payload.display = display;
        }
        await api.createAccount(payload);
        toast({ title: `已成功创建微信「${name}」`, tone: "good" });
        closeDialog(wizard);
        await reloadData();

        if (start) {
          startLogin(customId, name, { onSuccess: reloadData });
        }
      } catch (err) {
        renderWizardBody(
          {
            displayName: name,
            accountId: customId,
            runtimeProvider: provider,
            display,
            autostart,
            start,
          },
          { global: `创建失败：${err.message}` }
        );
      }
    };

    submitBtn.onclick = handleSubmit;
    form.onsubmit = (e) => {
      e.preventDefault();
      handleSubmit();
    };
  };

  renderWizardBody();
  openDialog(wizard);
}

// Listen to global event for opening add wizard
window.addEventListener("wechat-hub:open-add-wizard", () => {
  const management = state.runtimeManagement || {};
  const runtimeAvailable = Boolean(management.ok || (management.configured && management.available));
  if (!runtimeAvailable) {
    toast({ title: "微信管理暂时不可用", tone: "warn" });
    return;
  }
  openAddWizard(
    (state.runtimeAccounts || []).map((a) => accountViewModel(a, null)),
    async () => {}
  );
});
