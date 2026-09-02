/* Settings & Diagnostics View.
 *
 * Sections:
 * 1. 常规 (Appearance & theme switch, auto-refresh)
 * 2. 微信 (Runtime defaults)
 * 3. Telegram 集成 (EFB multi-account integration)
 * 4. AI 助手 (WeChat Agent status)
 * 5. 数据与存储 (Storage info)
 * 6. 高级与诊断 (Core health, contracts, runtime kv, log viewer)
 */

import { state } from "../state.js";
import { api } from "../api.js";
import { escapeHtml, escapeAttr, fmtDateTime, fmtNumber } from "../format.js";
import { icon } from "../icons.js";
import { toast } from "../components/toast.js";

let activeTab = "general"; // "general" | "wechat" | "telegram" | "ai" | "storage" | "diagnostics"
let loadedLogs = [];

/**
 * Render Settings View.
 * @param {HTMLElement} container
 * @param {() => Promise<void>} reloadData
 * @param {string} [subRoute=""]
 */
export function renderSettingsView(container, reloadData, subRoute = "") {
  if (subRoute === "advanced" || subRoute === "diagnostics") {
    activeTab = "diagnostics";
  }

  const status = state.status || {};
  const core = status.core || {};
  const sync = status.sync || {};
  const integrations = status.integrations || {};
  const runtimeMgmt = status.runtime_management || state.runtimeManagement || {};

  const tabs = [
    { id: "general", label: "常规" },
    { id: "wechat", label: "微信" },
    { id: "telegram", label: "Telegram 集成" },
    { id: "ai", label: "AI 助手" },
    { id: "storage", label: "数据与存储" },
    { id: "diagnostics", label: "高级与诊断" },
  ];

  const navButtonsHtml = tabs
    .map(
      (t) =>
        `<button class="settings-tab-btn" data-tab="${t.id}" aria-current="${
          activeTab === t.id ? "true" : "false"
        }">${escapeHtml(t.label)}</button>`
    )
    .join("");

  let panelHtml = "";

  switch (activeTab) {
    case "general": {
      const currentPref = window.__wechatHubTheme?.preference || "system";
      panelHtml = `
        <div class="section">
          <div class="section-head">
            <div class="section-head-text"><h2 class="section-title">常规</h2></div>
          </div>
          <div class="surface surface-flush">
            <div class="settings-group">
              <!-- Appearance / Theme -->
              <div class="settings-item">
                <div class="settings-item-body">
                  <div class="settings-item-title">外观</div>
                  <div class="settings-item-text">浅色更适合查看微信登录画面。</div>
                </div>
                <select class="select" id="themeSelect" style="width: 160px;" aria-label="主题外观">
                  <option value="system" ${currentPref === "system" ? "selected" : ""}>跟随系统</option>
                  <option value="light" ${currentPref === "light" ? "selected" : ""}>浅色</option>
                  <option value="dark" ${currentPref === "dark" ? "selected" : ""}>深色</option>
                </select>
              </div>

              <!-- Auto refresh -->
              <div class="settings-item">
                <div class="settings-item-body">
                  <div class="settings-item-title">自动刷新</div>
                  <div class="settings-item-text">页面回到前台或定时自动刷新状态。</div>
                </div>
                <label class="switch">
                  <input type="checkbox" id="autoRefreshSwitch" ${state.autoRefresh !== false ? "checked" : ""} aria-label="自动刷新" />
                  <span class="switch-track"></span>
                </label>
              </div>
            </div>
          </div>
        </div>
      `;
      break;
    }

    case "wechat": {
      panelHtml = `
        <div class="section">
          <div class="section-head">
            <div class="section-head-text"><h2 class="section-title">微信运行设置</h2></div>
          </div>
          <div class="surface surface-flush">
            <div class="settings-group">
              <div class="settings-item">
                <div class="settings-item-body">
                  <div class="settings-item-title">运行模式说明</div>
                  <div class="settings-item-text">默认使用「推荐模式（Beta）— AgentWechat」，每个账号在独立沙箱中运行；「兼容模式 — Legacy」用于向下兼容已有环境。</div>
                </div>
                <span class="pill" data-tone="brand">推荐模式（Beta）默认</span>
              </div>
            </div>
          </div>
        </div>
      `;
      break;
    }

    case "telegram": {
      const efb = integrations.efb || {};
      const isEfbOk = Boolean(efb.configured && efb.ok);
      panelHtml = `
        <div class="section">
          <div class="section-head">
            <div class="section-head-text"><h2 class="section-title">Telegram 集成</h2></div>
          </div>
          <div class="surface surface-flush">
            <div class="settings-group">
              <div class="settings-item">
                <div class="avatar avatar-sm">${icon("telegram", { size: "sm" })}</div>
                <div class="settings-item-body">
                  <div class="settings-item-title">Telegram 集成</div>
                  <div class="settings-item-text">${
                    isEfbOk
                      ? "Telegram 集成服务已连接并正常运行。"
                      : "未启用。启用后可以在 Telegram 中收发这些微信的消息。"
                  }</div>
                </div>
                <span class="pill" data-tone="${isEfbOk ? "brand" : "neutral"}">${
        isEfbOk ? "已启用" : "未启用"
      }</span>
              </div>
            </div>
          </div>
        </div>
      `;
      break;
    }

    case "ai": {
      const agent = integrations.agent || {};
      const isAgentOk = Boolean(agent.configured && agent.ok);
      panelHtml = `
        <div class="section">
          <div class="section-head">
            <div class="section-head-text"><h2 class="section-title">AI 助手</h2></div>
          </div>
          <div class="surface surface-flush">
            <div class="settings-group">
              <div class="settings-item">
                <div class="avatar avatar-sm">${icon("sparkle", { size: "sm" })}</div>
                <div class="settings-item-body">
                  <div class="settings-item-title">WeChat Agent</div>
                  <div class="settings-item-text">${
                    isAgentOk
                      ? "Agent 自动化与 AI 助手模块已连接。"
                      : "未配置或未运行。配置后可支持大模型智能接话与自动化。"
                  }</div>
                </div>
                <span class="pill" data-tone="${isAgentOk ? "brand" : "neutral"}">${
        isAgentOk ? "在线" : "未配置"
      }</span>
              </div>
            </div>
          </div>
        </div>
      `;
      break;
    }

    case "storage": {
      panelHtml = `
        <div class="section">
          <div class="section-head">
            <div class="section-head-text"><h2 class="section-title">数据与存储</h2></div>
          </div>
          <div class="surface surface-flush">
            <div class="settings-group">
              <div class="settings-item">
                <div class="avatar avatar-sm">${icon("database", { size: "sm" })}</div>
                <div class="settings-item-body">
                  <div class="settings-item-title">Console 本地持久化与归档</div>
                  <div class="settings-item-text">消息快照、事件游标与收藏附件保存在 Console 自有数据目录中，与 Core 解耦。</div>
                </div>
                <span class="pill">持久化存储</span>
              </div>
            </div>
          </div>
        </div>
      `;
      break;
    }

    case "diagnostics": {
      // Build Services List
      const isCoreOk = Boolean(core.ok);
      const isAgentOk = Boolean(integrations.agent?.ok);
      const isEfbOk = Boolean(integrations.efb?.ok);

      // Build Accounts details dl.kv
      const runtimeAccounts = state.runtimeAccounts || [];
      let accountsKvHtml = "";
      if (runtimeAccounts.length === 0) {
        accountsKvHtml = `<div style="padding: 16px; color: var(--text-secondary);">暂无运行中账号。</div>`;
      } else {
        accountsKvHtml = runtimeAccounts
          .map((a) => {
            const pids = Array.isArray(a.pids) ? a.pids.join(", ") : a.pid || "--";
            return `
              <div style="padding: 16px; border-bottom: 1px solid var(--border);">
                <div style="font-weight: var(--fw-semibold); margin-bottom: 8px;">${escapeHtml(
                  a.display_name || a.account_id
                )} (${escapeHtml(a.account_id)})</div>
                <dl class="kv">
                  <div><dt>runtime_provider</dt><dd>${escapeHtml(a.runtime_provider || "legacy")}</dd></div>
                  <div><dt>runtime_health</dt><dd>${escapeHtml(a.runtime_health || (a.running ? "running" : "stopped"))}</dd></div>
                  <div><dt>agent_server_healthy</dt><dd>${escapeHtml(String(a.agent_server_healthy ?? "--"))}</dd></div>
                  <div><dt>PID</dt><dd class="mono">${escapeHtml(String(pids))}</dd></div>
                  <div><dt>UID</dt><dd class="mono">${escapeHtml(String(a.uid ?? "--"))}</dd></div>
                  <div><dt>Display</dt><dd class="mono">${escapeHtml(String(a.display ?? "--"))}</dd></div>
                  <div><dt>HOME</dt><dd class="mono">${escapeHtml(String(a.home || "--"))}</dd></div>
                  <div><dt>Image</dt><dd class="mono" style="word-break: break-all;">${escapeHtml(String(a.current_image || a.image || "--"))}</dd></div>
                  <div><dt>自动启动</dt><dd>${a.autostart ? "true" : "false"}</dd></div>
                </dl>
              </div>
            `;
          })
          .join("");
      }

      panelHtml = `
        <!-- Service Status -->
        <div class="section">
          <div class="section-head">
            <div class="section-head-text"><h2 class="section-title">服务状态</h2></div>
          </div>
          <div class="surface surface-flush">
            <div class="rows">
              <div class="row">
                <div class="row-body">
                  <div class="row-title">
                    <strong>wechat-core</strong>
                    <span class="pill" data-tone="${isCoreOk ? "brand" : "danger"}">${
        isCoreOk ? "必需 · 在线" : "必需 · 异常"
      }</span>
                  </div>
                  <div class="row-meta mono">${escapeHtml(core.url || "http://127.0.0.1:8080")} · contract v${
        status.contract_version || 1
      }</div>
                </div>
              </div>
              <div class="row">
                <div class="row-body">
                  <div class="row-title">
                    <strong>wechat-agent</strong>
                    <span class="pill" data-tone="${isAgentOk ? "brand" : "neutral"}">${
        isAgentOk ? "可选 · 在线" : "可选 · 未配置"
      }</span>
                  </div>
                  <div class="row-meta">${
                    isAgentOk ? "自动化与模型助手已连接。" : "未配置属于正常状态。"
                  }</div>
                </div>
              </div>
              <div class="row">
                <div class="row-body">
                  <div class="row-title">
                    <strong>efb-multi</strong>
                    <span class="pill" data-tone="${isEfbOk ? "brand" : "neutral"}">${
        isEfbOk ? "可选 · 在线" : "可选 · 未配置"
      }</span>
                  </div>
                  <div class="row-meta">${
                    isEfbOk ? "Telegram 桥接集成运行中。" : "Telegram 集成未运行。"
                  }</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Account Runtime Details -->
        <div class="section">
          <div class="section-head">
            <div class="section-head-text"><h2 class="section-title">账号运行详情</h2></div>
          </div>
          <div class="surface surface-flush">${accountsKvHtml}</div>
        </div>

        <!-- Sync & Logs -->
        <div class="section">
          <div class="section-head">
            <div class="section-head-text">
              <h2 class="section-title">同步状态与日志</h2>
              <p>Core events 游标：${escapeHtml(sync.cursor || state.status?.messages?.cursor || "--")} · 最近同步：${fmtDateTime(sync.at)}</p>
            </div>
            <button class="btn btn-secondary btn-sm" id="diagSyncNowBtn">立即同步事件</button>
          </div>
          <div class="surface surface-flush">
            <div style="padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; gap: 8px; flex-wrap: wrap;">
              <select class="select" id="logLevelSelect" style="width: 120px; height: var(--control-h-sm); font-size: var(--fs-caption);" aria-label="日志级别">
                <option value="">全部等级</option>
                <option value="info">Info</option>
                <option value="warn">Warn</option>
                <option value="error">Error</option>
              </select>
              <div class="search" style="flex: 1; min-width: 160px;">
                ${icon("search", { size: "sm" })}
                <input class="input" id="logSearchInput" placeholder="搜索日志…" />
              </div>
            </div>
            <div class="log-list" id="diagLogListRoot" style="max-height: 320px; overflow-y: auto;">
              <div style="padding: 24px; text-align: center; color: var(--text-secondary);">正在加载日志…</div>
            </div>
          </div>
        </div>
      `;
      break;
    }
  }

  container.innerHTML = `
    <div class="page-inner wide">
      <div class="page-head">
        <div>
          <div class="page-title">${activeTab === "diagnostics" ? "高级与诊断" : "设置"}</div>
          ${
            activeTab === "diagnostics"
              ? `<p class="page-subtitle">技术信息集中在这一层，不影响主体验。</p>`
              : ""
          }
        </div>
      </div>

      <div class="settings-layout">
        <div class="settings-nav">${navButtonsHtml}</div>
        <div class="settings-panels">${panelHtml}</div>
      </div>
    </div>
  `;

  // Wire navigation tabs
  container.querySelectorAll(".settings-tab-btn").forEach((btn) => {
    btn.onclick = () => {
      activeTab = btn.dataset.tab;
      renderSettingsView(container, reloadData);
    };
  });

  // Wire Theme selector
  const themeSelect = container.querySelector("#themeSelect");
  if (themeSelect) {
    themeSelect.onchange = () => {
      window.__wechatHubTheme?.set(themeSelect.value);
    };
  }

  // Wire Auto Refresh switch
  const autoRefreshSwitch = container.querySelector("#autoRefreshSwitch");
  if (autoRefreshSwitch) {
    autoRefreshSwitch.onchange = () => {
      setState({ autoRefresh: autoRefreshSwitch.checked });
    };
  }

  // Wire Sync Now
  const syncNowBtn = container.querySelector("#diagSyncNowBtn");
  if (syncNowBtn) {
    syncNowBtn.onclick = async () => {
      syncNowBtn.disabled = true;
      try {
        await api.syncEvents();
        toast({ title: "已完成事件同步", tone: "good" });
        await reloadData();
      } catch (err) {
        toast({ title: "同步失败", text: err.message, tone: "bad" });
      } finally {
        syncNowBtn.disabled = false;
      }
    };
  }

  // Load and wire logs if on diagnostics tab
  if (activeTab === "diagnostics") {
    loadAndRenderLogs(container);
  }
}

async function loadAndRenderLogs(container) {
  const logListRoot = container.querySelector("#diagLogListRoot");
  const levelSelect = container.querySelector("#logLevelSelect");
  const searchInput = container.querySelector("#logSearchInput");
  if (!logListRoot) return;

  try {
    const res = await api.logs({ limit: 100 });
    loadedLogs = res.logs || [];
  } catch (err) {
    loadedLogs = [
      {
        timestamp: new Date().toISOString(),
        level: "info",
        category: "console",
        message: `读取日志就绪`,
      },
    ];
  }

  const renderRows = () => {
    const levelFilter = levelSelect?.value || "";
    const query = (searchInput?.value || "").trim().toLowerCase();

    const filtered = loadedLogs.filter((l) => {
      if (levelFilter && l.level !== levelFilter) return false;
      if (query && !(l.message || "").toLowerCase().includes(query) && !(l.category || "").toLowerCase().includes(query)) return false;
      return true;
    });

    if (filtered.length === 0) {
      logListRoot.innerHTML = `<div style="padding: 24px; text-align: center; color: var(--text-secondary);">暂无匹配日志</div>`;
      return;
    }

    logListRoot.innerHTML = filtered
      .map((l) => {
        const tone = l.level === "error" ? "bad" : l.level === "warn" ? "warn" : "";
        return `
          <div class="log-row">
            <span>${fmtDateTime(l.timestamp || l.created_at)}</span>
            <span class="log-level" ${tone ? `data-tone="${tone}"` : ""}>${escapeHtml(l.level || "info")}</span>
            <span>${escapeHtml(l.category || "sys")}</span>
            <span class="log-message">${escapeHtml(l.message || "")}</span>
          </div>
        `;
      })
      .join("");
  };

  if (levelSelect) levelSelect.onchange = renderRows;
  if (searchInput) searchInput.oninput = renderRows;
  renderRows();
}
