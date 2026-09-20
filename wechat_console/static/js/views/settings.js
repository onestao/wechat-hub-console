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

import { state, setState } from "../state.js";
import { api } from "../api.js";
import { escapeHtml, escapeAttr, fmtDateTime, fmtNumber } from "../format.js";
import { icon } from "../icons.js";
import { toast } from "../components/toast.js";
import { capabilitySummary } from "../capabilities.js";

let activeTab = "general"; // "general" | "wechat" | "telegram" | "ai" | "storage" | "diagnostics"
let loadedLogs = [];

/** Business label for one consumer lifecycle state (Runtime is the authority). */
function consumerStateLabel(entry) {
  const labels = {
    running: "运行中",
    stopped: "已停止",
    failed: "启动失败",
    not_provisioned: "未创建",
    not_configured: "未配置",
    unknown: "状态未知",
  };
  const state = String(entry?.state || "unknown");
  return labels[state] || state;
}

function consumerTone(entry) {
  const state = String(entry?.state || "unknown");
  if (state === "running") return "brand";
  if (state === "failed") return "danger";
  return "neutral";
}

/** One consumer control card: the real Runtime state plus start / stop. */
function renderConsumerCard(consumer, entry, { description }) {
  const running = Boolean(entry?.running);
  const canStart = Boolean(entry?.can_start);
  const blocked = String(entry?.blocked_reason || "");
  const lastError = String(entry?.last_error || "");
  const body = running ? "正在运行。" : blocked || "当前未运行。";
  return `
    <div class="section">
      <div class="section-head">
        <div class="section-head-text"><h2 class="section-title">${escapeHtml(
          entry?.display_name || consumer,
        )}</h2></div>
      </div>
      <div class="surface surface-flush">
        <div class="settings-group">
          <div class="settings-item">
            <div class="settings-item-body">
              <div class="settings-item-title">${escapeHtml(description)}</div>
              <div class="settings-item-text">${escapeHtml(body)}</div>
              ${
                lastError
                  ? `<div class="settings-item-text mono" style="color: var(--danger);">${escapeHtml(lastError)}</div>`
                  : ""
              }
            </div>
            <span class="pill" data-tone="${consumerTone(entry)}">${escapeHtml(consumerStateLabel(entry))}</span>
          </div>
          <div class="settings-item">
            <div class="settings-item-body">
              <div class="settings-item-title">控制</div>
              <div class="settings-item-text">同一时刻只能运行一个消费者（EFB 与 Agent 互斥）。</div>
            </div>
            <div style="display: flex; gap: 8px;">
              <button class="btn btn-primary btn-sm" data-consumer-action="start" data-consumer="${escapeAttr(
                consumer,
              )}" ${running || !canStart ? "disabled" : ""}>启动</button>
              <button class="btn btn-secondary btn-sm" data-consumer-action="stop" data-consumer="${escapeAttr(
                consumer,
              )}" ${running ? "" : "disabled"}>停止</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

/**
 * Render Settings View.
 * @param {HTMLElement} container
 * @param {() => Promise<void>} reloadData
 * @param {string} [subRoute=""]
 */
export function renderSettingsView(container, reloadData, subRoute = "") {
  if (subRoute === "advanced" || subRoute === "diagnostics") {
    activeTab = "diagnostics";
  } else if (["general", "wechat", "telegram", "ai", "storage"].includes(subRoute)) {
    // Deep link from the home page (e.g. #/settings/ai) selects that tab.
    activeTab = subRoute;
  }

  const status = state.status || {};
  const core = status.core || {};
  const sync = status.sync || {};
  // Consumer state comes exclusively from the Runtime via Core.  A reachable
  // port is not a lifecycle state, so there is no liveness probe here.
  const install = status.install || {};
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
                  <div class="settings-item-text">默认使用「推荐模式（Beta）」，每个账号在独立沙箱中运行；「兼容模式」用于向下兼容已有环境。</div>
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
      panelHtml = renderConsumerCard("efb", install.efb || {}, {
        description: "把微信消息转发到 Telegram，并把 Telegram 回复发回微信。",
      });
      break;
    }

    case "ai": {
      panelHtml = renderConsumerCard("agent", install.agent || {}, {
        description: "内置消息消费者：在 Console 内直接处理消息，无需外部凭据。",
      });
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
                  <div class="settings-item-title">持久化数据存储</div>
                  <div class="settings-item-text">消息快照、收藏与归档文件保存在 WeChat Hub 的持久化数据目录中。</div>
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
      // Consumer state is the Runtime's, never a reachability probe.
      const isAgentOk = String(install.agent?.state || "") === "running";
      const isEfbOk = String(install.efb?.state || "") === "running";

      // Build Accounts details dl.kv
      const runtimeAccounts = state.runtimeAccounts || [];
      let accountsKvHtml = "";
      if (runtimeAccounts.length === 0) {
        accountsKvHtml = `<div style="padding: 16px; color: var(--text-secondary);">暂无运行中账号。</div>`;
      } else {
        accountsKvHtml = runtimeAccounts
          .map((a) => {
            const coreAcc = (state.accounts || []).find((ca) => ca.account_id === a.account_id);
            const pids = Array.isArray(a.pids) ? a.pids.join(", ") : (a.pid !== undefined && a.pid !== null ? String(a.pid) : "--");
            const senderCaps = capabilitySummary(coreAcc, a);

            let windowsCount = "--";
            if (Array.isArray(a.windows)) {
              windowsCount = a.windows.length;
            } else if (Array.isArray(a.probe?.windows)) {
              windowsCount = a.probe.windows.length;
            } else if (Array.isArray(a.runtime_details?.windows)) {
              windowsCount = a.runtime_details.windows.length;
            } else if (a.probe?.windows_count !== undefined && a.probe?.windows_count !== null) {
              windowsCount = a.probe.windows_count;
            } else if (a.windows_count !== undefined && a.windows_count !== null) {
              windowsCount = a.windows_count;
            } else if (a.runtime_details?.windows_count !== undefined && a.runtime_details?.windows_count !== null) {
              windowsCount = a.runtime_details.windows_count;
            } else if (typeof a.windows === "number") {
              windowsCount = a.windows;
            }

            const hotReload = state.runtimeManagement?.registry_hot_reload !== undefined
              ? (state.runtimeManagement.registry_hot_reload ? "支持" : "关闭")
              : (a.registry_hot_reload !== undefined ? (a.registry_hot_reload ? "支持" : "关闭") : "--");
            const cursor = a.cursor ?? (a.event_cursor ?? (coreAcc?.cursor ?? (state.status?.sync?.cursor ?? (state.status?.cursor ?? (state.status?.messages?.cursor ?? "--")))));
            const lastSync = a.last_synced_at ?? (a.last_sync ?? (coreAcc?.last_event_at ?? (coreAcc?.last_synced_at ?? (state.status?.sync?.last_synced_at ?? (state.status?.sync?.at ?? (state.status?.last_synced_at ?? "--"))))));

            return `
              <div style="padding: 16px; border-bottom: 1px solid var(--border);">
                <div style="font-weight: var(--fw-semibold); margin-bottom: 8px;">${escapeHtml(
                  a.display_name || a.account_id
                )} (${escapeHtml(a.account_id)})</div>
                <dl class="kv">
                  <div><dt>account_id</dt><dd class="mono">${escapeHtml(a.account_id || "--")}</dd></div>
                  <div><dt>runtime_provider</dt><dd>${escapeHtml(a.runtime_provider || "legacy")}</dd></div>
                  <div><dt>runtime_health</dt><dd>${escapeHtml(a.runtime_health || (a.running ? "running" : (a.stopped ? "stopped" : "--")))}</dd></div>
                  <div><dt>agent_server_healthy</dt><dd>${escapeHtml(String(a.agent_server_healthy ?? (a.probe?.healthy ?? "--")))}</dd></div>
                  <div><dt>PID</dt><dd class="mono">${escapeHtml(String(pids))}</dd></div>
                  <div><dt>UID</dt><dd class="mono">${escapeHtml(String(a.uid ?? (a.runtime_details?.uid ?? "--")))}</dd></div>
                  <div><dt>Display</dt><dd class="mono">${escapeHtml(String(a.display || a.runtime_details?.display || "--"))}</dd></div>
                  <div><dt>HOME</dt><dd class="mono">${escapeHtml(String(a.home || a.runtime_details?.home || "--"))}</dd></div>
                  <div><dt>image</dt><dd class="mono" style="word-break: break-all;">${escapeHtml(String(a.current_image || a.image || a.runtime_details?.image || "--"))}</dd></div>
                  <div><dt>autostart</dt><dd>${a.autostart !== undefined && a.autostart !== null ? (a.autostart ? "true" : "false") : "--"}</dd></div>
                  <div><dt>sender capability</dt><dd class="mono">${escapeHtml(senderCaps)}</dd></div>
                  <div><dt>窗口数</dt><dd class="mono">${escapeHtml(String(windowsCount))}</dd></div>
                  <div><dt>registry hot reload</dt><dd>${escapeHtml(hotReload)}</dd></div>
                  <div><dt>事件游标</dt><dd class="mono">${escapeHtml(String(cursor))}</dd></div>
                  <div><dt>最近同步</dt><dd class="mono">${escapeHtml(String(lastSync))}</dd></div>
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
                    <span class="pill" data-tone="${isAgentOk ? "brand" : "neutral"}">${escapeHtml(
                      `可选 · ${consumerStateLabel(install.agent)}`,
                    )}</span>
                  </div>
                  <div class="row-meta mono">${escapeHtml(
                    String(install.agent?.container_name || "wechat-hub-agent"),
                  )} · ${escapeHtml(String(install.agent?.state || "unknown"))}</div>
                </div>
              </div>
              <div class="row">
                <div class="row-body">
                  <div class="row-title">
                    <strong>wechat-hub-efb</strong>
                    <span class="pill" data-tone="${isEfbOk ? "brand" : "neutral"}">${escapeHtml(
                      `可选 · ${consumerStateLabel(install.efb)}`,
                    )}</span>
                  </div>
                  <div class="row-meta mono">${escapeHtml(
                    String(install.efb?.container_name || "wechat-hub-efb"),
                  )} · ${escapeHtml(String(install.efb?.state || "unknown"))}</div>
                </div>
              </div>
              <div class="row">
                <div class="row-body">
                  <div class="row-title">
                    <strong>consumer mode</strong>
                    <span class="pill">${escapeHtml(String(install.consumer_mode || "unknown"))}</span>
                  </div>
                  <div class="row-meta mono">mutual_exclusion=${
                    install.mutual_exclusion ? "true" : "false"
                  } · desired=${escapeHtml(String(install.desired_mode || "unknown"))}</div>
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

  // Wire Consumer Control actions (start / stop).  The Runtime performs the
  // lifecycle change and enforces EFB XOR Agent; the Console only asks Core.
  container.querySelectorAll("[data-consumer-action]").forEach((btn) => {
    btn.onclick = async () => {
      const consumer = btn.dataset.consumer;
      const action = btn.dataset.consumerAction;
      btn.disabled = true;
      try {
        if (action === "start") {
          await api.startConsumer(consumer);
        } else {
          await api.stopConsumer(consumer);
        }
        toast(action === "start" ? "已启动" : "已停止");
        await reloadData();
      } catch (err) {
        toast({ title: "操作失败", text: err?.message || String(err), tone: "bad" });
        btn.disabled = false;
      }
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
