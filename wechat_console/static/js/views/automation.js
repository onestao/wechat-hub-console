/* Automation View.
 *
 * Real management surface for the optional WeChat Agent service
 * (monitors = 自动回复/消息关注, schedules = 定时任务, templates = 消息模板).
 *
 * Truthfulness rules (Agent F taskbook):
 * - Everything listed here comes from the Agent service via the Console proxy;
 *   when the Agent is absent this page says so instead of implying features.
 * - "已启用" always reflects a persisted rule; execution proof is queryable
 *   through the per-rule run log (rule -> runs -> receipt/error).
 * - send_text rules and schedules are bound to one WeChat identity at creation
 *   and the Agent re-validates the live binding before every execution.
 */

import { state } from "../state.js";
import { api, ApiError } from "../api.js";
import { escapeHtml, escapeAttr, fmtDateTime } from "../format.js";
import { icon } from "../icons.js";
import { openDialog, closeDialog } from "../components/dialog.js";
import { confirmAction } from "../components/confirm.js";
import { toast } from "../components/toast.js";

const ACTION_LABELS = {
  send_text: "自动回复",
  record: "记录",
  summary: "AI 总结",
  image_understanding: "图片理解",
};

const TASK_LABELS = {
  send_text: "定时发送",
  record: "定时记录",
  summary: "定时总结",
};

const INTERVAL_OPTIONS = [
  { value: 60, label: "每 1 分钟" },
  { value: 300, label: "每 5 分钟" },
  { value: 900, label: "每 15 分钟" },
  { value: 1800, label: "每 30 分钟" },
  { value: 3600, label: "每 1 小时" },
  { value: 21600, label: "每 6 小时" },
  { value: 86400, label: "每 1 天" },
];

const AGENT_UNCONFIGURED_TEXT = "当前部署未启用自动化服务（WeChat Agent），当前版本尚未提供此自动化能力。";

/**
 * Render Automation View.
 * @param {HTMLElement} container
 * @param {() => Promise<void>} reloadData
 */
export function renderAutomationView(container, reloadData) {
  container.innerHTML = `
    <div class="page-inner">
      <div class="page-head">
        <div>
          <div class="page-title">自动化</div>
          <p class="page-subtitle">自动回复、消息关注、定时任务和消息模板，由 WeChat Agent 服务执行。</p>
        </div>
        <button class="btn btn-ghost btn-sm" id="automationRefreshBtn" aria-label="刷新">
          ${icon("refresh", { size: "sm" })} 刷新
        </button>
      </div>
      <div id="automationBody"><div class="surface"><div class="empty"><div class="empty-title">正在加载自动化状态…</div></div></div></div>
    </div>
  `;

  const body = container.querySelector("#automationBody");
  const refreshBtn = container.querySelector("#automationRefreshBtn");
  let refreshSeq = 0;

  async function load() {
    const seq = ++refreshSeq;
    // Consumer state comes from the Runtime via Core; the Agent's functional
    // API is only called once the Runtime says the consumer is running.
    const agentConsumer = state.status?.install?.agent || {};
    if (String(agentConsumer.state || "") !== "running") {
      if (seq === refreshSeq) renderUnconfigured(body, agentConsumer);
      return;
    }
    const [statusRes, monitorsRes, schedulesRes, templatesRes] = await Promise.allSettled([
      api.agentStatus(),
      api.agentMonitors(),
      api.agentSchedules(),
      api.agentTemplates(),
    ]);
    if (seq !== refreshSeq) return; // a newer refresh superseded this one
    if (statusRes.status !== "fulfilled") {
      renderUnreachable(body, statusRes.reason);
      return;
    }
    const agentStatus = statusRes.value.agent || {};
    const monitors = monitorsRes.status === "fulfilled" ? monitorsRes.value.monitors || [] : null;
    const schedules = schedulesRes.status === "fulfilled" ? schedulesRes.value.schedules || [] : null;
    const templates = templatesRes.status === "fulfilled" ? templatesRes.value.templates || [] : null;
    renderManaged(body, {
      agentStatus,
      monitors: monitors || [],
      schedules: schedules || [],
      templates: templates || [],
      partial: monitors === null || schedules === null || templates === null,
      reloadData,
    });
  }

  refreshBtn.onclick = () => load();
  load();
}

/* ---------------------------------------------------------------- states */

function renderUnconfigured(container, entry = {}) {
  const stateLabel = {
    running: "运行中",
    stopped: "已停止",
    failed: "启动失败",
    not_configured: "未配置",
    not_provisioned: "未创建",
  }[String(entry?.state || "")] || "未运行";
  const detail = String(entry?.last_error || entry?.blocked_reason || "");
  container.innerHTML = `
    <div class="surface">
      <div class="empty">
        <div class="empty-icon">${icon("automation")}</div>
        <div class="empty-title">自动化服务未启用（${escapeHtml(stateLabel)}）</div>
        <p class="empty-text">${escapeHtml(AGENT_UNCONFIGURED_TEXT)}</p>
        <p class="empty-text">请在「设置 → AI 助手」中启动 Agent 消费者。自动化功能由 Runtime 管理其生命周期。</p>
        ${detail ? `<p class="empty-text mono" style="color: var(--text-secondary);">${escapeHtml(detail)}</p>` : ""}
      </div>
    </div>
    ${renderAiCapabilityNote()}
  `;
}

function renderUnreachable(container, reason) {
  const message = reason instanceof ApiError ? reason.message : String(reason || "未知错误");
  container.innerHTML = `
    <div class="surface">
      <div class="empty">
        <div class="empty-icon">${icon("alertTriangle")}</div>
        <div class="empty-title">无法连接自动化服务</div>
        <p class="empty-text">WeChat Agent 已配置，但当前不可访问，本页面无法展示真实的自动化状态。</p>
        <p class="empty-text mono" style="color: var(--text-secondary);">${escapeHtml(message)}</p>
      </div>
    </div>
  `;
}

/** F8: AI assistant is honest about what exists and what does not. */
function renderAiCapabilityNote() {
  return `
    <div class="section">
      <div class="section-head">
        <div class="section-head-text">
          <h2 class="section-title">AI 能力说明</h2>
        </div>
      </div>
      <div class="surface surface-flush">
        <div class="rows">
          <div class="row">
            <div class="avatar avatar-sm">${icon("sparkle", { size: "sm" })}</div>
            <div class="row-body">
              <div class="row-title"><strong>AI 总结 / 图片理解（规则动作）</strong></div>
              <div class="row-meta">可以作为规则的执行动作使用，需要 Agent 侧已配置大模型（LLM）。</div>
            </div>
          </div>
          <div class="row">
            <div class="avatar avatar-sm">${icon("sparkle", { size: "sm" })}</div>
            <div class="row-body">
              <div class="row-title"><strong>独立 AI 助手对话</strong></div>
              <div class="row-meta">当前版本尚未提供此自动化能力。模型接口存在，但完整的对话助手工作流尚未实现。</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

/* -------------------------------------------------------------- main view */

function renderManaged(
  container,
  { agentStatus, monitors, schedules, templates, partial, reloadData }
) {
  const counts = agentStatus.counts || {};
  const workers = agentStatus.workers || {};
  const coreOk = Boolean(agentStatus.core?.ok);

  container.innerHTML = `
    <div class="surface" style="padding: 24px;">
      <div style="display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="avatar" data-tone="${coreOk ? "good" : "warn"}">${icon("automation")}</div>
          <div>
            <div class="item-title">WeChat Agent ${coreOk ? "已连接" : "已连接（Core 异常）"}</div>
            <p class="caption" style="margin-top: 2px;">
              规则 ${fmtNumber(monitors.length)} · 定时任务 ${fmtNumber(schedules.length)} · 模板 ${fmtNumber(templates.length)}
              · 记录 ${fmtNumber(counts.records)}
            </p>
          </div>
        </div>
        <span class="pill" data-tone="${coreOk ? "brand" : "warn"}">${coreOk ? "运行正常" : "Core 连接异常"}</span>
      </div>
    </div>
    ${partial ? `<div class="surface" style="margin-top: 12px; padding: 12px 16px;"><p class="caption" style="margin:0;">部分数据加载失败，以下列表可能不完整；请刷新重试。</p></div>` : ""}
    <div class="section">
      <div class="section-head">
        <div class="section-head-text">
          <h2 class="section-title">自动回复与消息关注</h2>
          <p class="caption">按关键词或会话触发回复、记录或总结；规则绑定单个微信身份，身份变化时自动停止执行。</p>
        </div>
        <button class="btn btn-primary btn-sm" id="newMonitorBtn">新建规则</button>
      </div>
      <div class="surface surface-flush" id="monitorRows"></div>
    </div>
    <div class="section">
      <div class="section-head">
        <div class="section-head-text">
          <h2 class="section-title">定时任务</h2>
          <p class="caption">按固定间隔重复执行；定时发送在创建时绑定身份，每次执行前重新校验，身份不符即停止。</p>
        </div>
        <button class="btn btn-primary btn-sm" id="newScheduleBtn">新建定时任务</button>
      </div>
      <div class="surface surface-flush" id="scheduleRows"></div>
    </div>
    <div class="section">
      <div class="section-head">
        <div class="section-head-text">
          <h2 class="section-title">消息模板</h2>
          <p class="caption">供规则与定时任务引用的文案模板，支持 {{message.text}} 等占位符。</p>
        </div>
        <button class="btn btn-primary btn-sm" id="newTemplateBtn">新建模板</button>
      </div>
      <div class="surface surface-flush" id="templateRows"></div>
    </div>
    ${renderAiCapabilityNote()}
  `;

  renderMonitorRows(container, monitors, reloadData);
  renderScheduleRows(container, schedules, reloadData);
  renderTemplateRows(container, templates, reloadData);

  container.querySelector("#newMonitorBtn").onclick = () => openMonitorDialog(null, reloadData);
  container.querySelector("#newScheduleBtn").onclick = () => openScheduleDialog(null, reloadData);
  container.querySelector("#newTemplateBtn").onclick = () => openTemplateDialog(null, reloadData);
}

function fmtNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function identityBadge(account) {
  const binding = String(account?.identity_binding_state || "");
  if (binding === "bound") return `<span class="pill" data-tone="good">已绑定</span>`;
  if (binding === "mismatch") return `<span class="pill" data-tone="bad">身份冲突</span>`;
  if (binding === "unresolved") return `<span class="pill" data-tone="warn">未确认</span>`;
  return `<span class="pill" data-tone="warn">未绑定</span>`;
}

function accountLabel(accountId) {
  const account = (state.accounts || []).find((row) => row.account_id === accountId);
  if (!account) return { text: accountId || "（未知账号）", account: null };
  const name = account.display_name || account.wechat_profile?.nickname || account.account_id;
  return { text: name, account };
}

function statusPill(status) {
  if (status === "success") return `<span class="pill" data-tone="good">成功</span>`;
  if (status === "failed") return `<span class="pill" data-tone="bad">失败</span>`;
  return `<span class="pill" data-tone="warn">${escapeHtml(String(status || "—"))}</span>`;
}

/* --------------------------------------------------------------- monitors */

function renderMonitorRows(container, monitors, reloadData) {
  const wrap = container.querySelector("#monitorRows");
  if (!monitors.length) {
    wrap.innerHTML = `<div class="empty"><div class="empty-title">还没有规则</div><p class="empty-text">新建一条规则，在匹配到消息时自动回复、记录或总结。</p></div>`;
    return;
  }
  wrap.innerHTML = monitors
    .map((monitor) => {
      const scope = accountLabel(monitor.account_id);
      const keyword = String(monitor.contains_text || "").trim();
      const actionLabel = ACTION_LABELS[monitor.action] || monitor.action;
      return `
        <div class="row" data-monitor-id="${escapeAttr(monitor.monitor_id)}">
          <div class="avatar avatar-sm">${icon(monitor.action === "send_text" ? "message" : "bell", { size: "sm" })}</div>
          <div class="row-body">
            <div class="row-title"><strong>${escapeHtml(monitor.name || monitor.monitor_id)}</strong>
              <span class="pill" data-tone="brand">${escapeHtml(actionLabel)}</span>
              ${monitor.enabled ? `<span class="pill" data-tone="good">已启用</span>` : `<span class="pill">已停用</span>`}
            </div>
            <div class="row-meta">
              范围：${escapeHtml(scope.text)}
              ${keyword ? ` · 关键词「${escapeHtml(keyword)}」` : ""}
              ${monitor.chat_id ? ` · 会话 ${escapeHtml(monitor.chat_id)}` : " · 全部会话"}
            </div>
          </div>
          <div style="display:flex; gap:6px; flex-shrink:0;">
            <button class="btn btn-ghost btn-sm" data-act="runs">执行记录</button>
            <button class="btn btn-ghost btn-sm" data-act="toggle">${monitor.enabled ? "停用" : "启用"}</button>
            <button class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button class="btn btn-ghost btn-sm" data-act="delete">删除</button>
          </div>
        </div>
      `;
    })
    .join("");

  wrap.querySelectorAll(".row").forEach((rowEl) => {
    const monitor = monitors.find((item) => item.monitor_id === rowEl.dataset.monitorId);
    if (!monitor) return;
    rowEl.querySelector('[data-act="edit"]').onclick = () => openMonitorDialog(monitor, reloadData);
    rowEl.querySelector('[data-act="toggle"]').onclick = async () => {
      await saveWithFeedback(
        () => api.saveAgentMonitor({ ...monitor, enabled: !monitor.enabled }),
        monitor.enabled ? "规则已停用" : "规则已启用",
        reloadData
      );
    };
    rowEl.querySelector('[data-act="delete"]').onclick = async () => {
      const ok = await confirmAction({
        title: "删除规则",
        text: `确定删除规则「${monitor.name || monitor.monitor_id}」吗？执行记录会一并删除。`,
        confirmLabel: "删除",
        tone: "danger",
      });
      if (!ok) return;
      await saveWithFeedback(() => api.deleteAgentMonitor(monitor.monitor_id), "规则已删除", reloadData);
    };
    rowEl.querySelector('[data-act="runs"]').onclick = () => showMonitorRuns(monitor);
  });
}

async function showMonitorRuns(monitor) {
  const dialog = ensureRunsDialog();
  dialog.querySelector("#runsTitle").textContent = `执行记录 · ${monitor.name || monitor.monitor_id}`;
  const bodyEl = dialog.querySelector("#runsBody");
  bodyEl.innerHTML = `<div class="empty"><div class="empty-title">正在加载…</div></div>`;
  openDialog(dialog);
  try {
    const payload = await api.agentMonitorRuns(monitor.monitor_id);
    const runs = payload.runs || [];
    if (!runs.length) {
      bodyEl.innerHTML = `<div class="empty"><div class="empty-title">暂无执行记录</div><p class="empty-text">规则触发后会在这里显示每次执行的结果。</p></div>`;
      return;
    }
    bodyEl.innerHTML = `
      <div class="rows">
        ${runs
          .map((run) => {
            const identity = run.result?.identity || {};
            return `
              <div class="row">
                <div class="row-body">
                  <div class="row-title">${statusPill(run.status)} <span class="caption">${escapeHtml(fmtDateTime(run.created_at))}</span></div>
                  <div class="row-meta">
                    ${identity.wechat_identity_uuid ? `身份 ${escapeHtml(identity.wechat_identity_uuid)} · 实例 ${escapeHtml(identity.instance_uuid || "—")}` : "无身份记录"}
                    ${run.error ? ` · ${escapeHtml(run.error)}` : ""}
                  </div>
                </div>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
  } catch (err) {
    bodyEl.innerHTML = `<div class="empty"><div class="empty-title">无法加载执行记录</div><p class="empty-text">${escapeHtml(String(err.message || err))}</p></div>`;
  }
}

/* -------------------------------------------------------------- schedules */

function renderScheduleRows(container, schedules, reloadData) {
  const wrap = container.querySelector("#scheduleRows");
  if (!schedules.length) {
    wrap.innerHTML = `<div class="empty"><div class="empty-title">还没有定时任务</div><p class="empty-text">新建一个定时任务，按固定间隔发送、记录或总结。</p></div>`;
    return;
  }
  wrap.innerHTML = schedules
    .map((schedule) => {
      const scope = accountLabel(schedule.account_id);
      const interval = INTERVAL_OPTIONS.find((opt) => opt.value === Number(schedule.interval_seconds));
      const taskLabel = TASK_LABELS[schedule.task_type] || schedule.task_type;
      return `
        <div class="row" data-schedule-id="${escapeAttr(schedule.schedule_id)}">
          <div class="avatar avatar-sm">${icon("clock", { size: "sm" })}</div>
          <div class="row-body">
            <div class="row-title"><strong>${escapeHtml(schedule.name || schedule.schedule_id)}</strong>
              <span class="pill" data-tone="brand">${escapeHtml(taskLabel)}</span>
              ${schedule.enabled ? `<span class="pill" data-tone="good">已启用</span>` : `<span class="pill">已停用</span>`}
            </div>
            <div class="row-meta">
              ${escapeHtml(scope.text)} · ${escapeHtml(interval ? interval.label : `${schedule.interval_seconds}s`)}
              · 下次执行 ${escapeHtml(fmtDateTime(schedule.next_run_at)) || "—"}
              · 上次执行 ${escapeHtml(fmtDateTime(schedule.last_run_at)) || "未执行"}
            </div>
          </div>
          <div style="display:flex; gap:6px; flex-shrink:0;">
            <button class="btn btn-ghost btn-sm" data-act="runs">执行记录</button>
            <button class="btn btn-ghost btn-sm" data-act="toggle">${schedule.enabled ? "停用" : "启用"}</button>
            <button class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button class="btn btn-ghost btn-sm" data-act="delete">删除</button>
          </div>
        </div>
      `;
    })
    .join("");

  wrap.querySelectorAll(".row").forEach((rowEl) => {
    const schedule = schedules.find((item) => item.schedule_id === rowEl.dataset.scheduleId);
    if (!schedule) return;
    rowEl.querySelector('[data-act="edit"]').onclick = () => openScheduleDialog(schedule, reloadData);
    rowEl.querySelector('[data-act="toggle"]').onclick = async () => {
      await saveWithFeedback(
        () => api.saveAgentSchedule({ ...schedule, enabled: !schedule.enabled }),
        schedule.enabled ? "定时任务已停用" : "定时任务已启用",
        reloadData
      );
    };
    rowEl.querySelector('[data-act="delete"]').onclick = async () => {
      const ok = await confirmAction({
        title: "删除定时任务",
        text: `确定删除定时任务「${schedule.name || schedule.schedule_id}」吗？执行记录会一并删除。`,
        confirmLabel: "删除",
        tone: "danger",
      });
      if (!ok) return;
      await saveWithFeedback(() => api.deleteAgentSchedule(schedule.schedule_id), "定时任务已删除", reloadData);
    };
    rowEl.querySelector('[data-act="runs"]').onclick = () => showScheduleRuns(schedule);
  });
}

async function showScheduleRuns(schedule) {
  const dialog = ensureRunsDialog();
  dialog.querySelector("#runsTitle").textContent = `执行记录 · ${schedule.name || schedule.schedule_id}`;
  const bodyEl = dialog.querySelector("#runsBody");
  bodyEl.innerHTML = `<div class="empty"><div class="empty-title">正在加载…</div></div>`;
  openDialog(dialog);
  try {
    const payload = await api.agentScheduleRuns(schedule.schedule_id);
    const runs = payload.runs || [];
    if (!runs.length) {
      bodyEl.innerHTML = `<div class="empty"><div class="empty-title">暂无执行记录</div><p class="empty-text">任务到点执行后会在这里显示每次结果。</p></div>`;
      return;
    }
    bodyEl.innerHTML = `
      <div class="rows">
        ${runs
          .map((run) => {
            const identity = run.result?.identity || {};
            return `
              <div class="row">
                <div class="row-body">
                  <div class="row-title">${statusPill(run.status)} <span class="caption">${escapeHtml(fmtDateTime(run.created_at))}</span></div>
                  <div class="row-meta">
                    ${identity.wechat_identity_uuid ? `身份 ${escapeHtml(identity.wechat_identity_uuid)}` : "无身份记录"}
                    ${run.error ? ` · ${escapeHtml(run.error)}` : ""}
                  </div>
                </div>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
  } catch (err) {
    bodyEl.innerHTML = `<div class="empty"><div class="empty-title">无法加载执行记录</div><p class="empty-text">${escapeHtml(String(err.message || err))}</p></div>`;
  }
}

/* -------------------------------------------------------------- templates */

function renderTemplateRows(container, templates, reloadData) {
  const wrap = container.querySelector("#templateRows");
  if (!templates.length) {
    wrap.innerHTML = `<div class="empty"><div class="empty-title">还没有模板</div><p class="empty-text">模板用于复用回复或记录文案。</p></div>`;
    return;
  }
  wrap.innerHTML = templates
    .map(
      (template) => `
        <div class="row" data-template-id="${escapeAttr(template.template_id)}">
          <div class="avatar avatar-sm">${icon("file", { size: "sm" })}</div>
          <div class="row-body">
            <div class="row-title"><strong>${escapeHtml(template.name || template.template_id)}</strong>
              ${template.enabled ? `<span class="pill" data-tone="good">已启用</span>` : `<span class="pill">已停用</span>`}
            </div>
            <div class="row-meta" style="max-width: 480px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(template.body || "")}</div>
          </div>
          <div style="display:flex; gap:6px; flex-shrink:0;">
            <button class="btn btn-ghost btn-sm" data-act="toggle">${template.enabled ? "停用" : "启用"}</button>
            <button class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button class="btn btn-ghost btn-sm" data-act="delete">删除</button>
          </div>
        </div>
      `
    )
    .join("");

  wrap.querySelectorAll(".row").forEach((rowEl) => {
    const template = templates.find((item) => item.template_id === rowEl.dataset.templateId);
    if (!template) return;
    rowEl.querySelector('[data-act="edit"]').onclick = () => openTemplateDialog(template, reloadData);
    rowEl.querySelector('[data-act="toggle"]').onclick = async () => {
      await saveWithFeedback(
        () => api.saveAgentTemplate({ ...template, enabled: !template.enabled }),
        template.enabled ? "模板已停用" : "模板已启用",
        reloadData
      );
    };
    rowEl.querySelector('[data-act="delete"]').onclick = async () => {
      const ok = await confirmAction({
        title: "删除模板",
        text: `确定删除模板「${template.name || template.template_id}」吗？引用它的规则将回退到直接文案。`,
        confirmLabel: "删除",
        tone: "danger",
      });
      if (!ok) return;
      await saveWithFeedback(() => api.deleteAgentTemplate(template.template_id), "模板已删除", reloadData);
    };
  });
}

/* --------------------------------------------------------------- dialogs */

function accountOptions(selectedId, { requireBound = false } = {}) {
  const accounts = state.accounts || [];
  return accounts
    .map((account) => {
      const bound = account.identity_binding_state === "bound" && account.wechat_identity_uuid;
      if (requireBound && !bound) return "";
      const selected = account.account_id === selectedId ? "selected" : "";
      const name = account.display_name || account.wechat_profile?.nickname || account.account_id;
      const suffix = bound ? "" : "（未绑定身份，不可自动发送）";
      return `<option value="${escapeAttr(account.account_id)}" ${selected}>${escapeHtml(name + suffix)}</option>`;
    })
    .join("");
}

function findAccount(accountId) {
  return (state.accounts || []).find((row) => row.account_id === accountId) || null;
}

function fieldError(dialog, id, message) {
  const holder = dialog.querySelector(`#${id}Error`);
  const field = dialog.querySelector(`#${id}`).closest(".field");
  if (!message) {
    if (holder) holder.remove();
    if (field) field.classList.remove("has-error");
    return;
  }
  if (field) field.classList.add("has-error");
  if (holder) {
    holder.textContent = message;
  } else if (field) {
    const p = document.createElement("p");
    p.className = "field-error";
    p.id = `${id}Error`;
    p.textContent = message;
    field.appendChild(p);
  }
}

function openMonitorDialog(monitor, reloadData) {
  const isEdit = Boolean(monitor);
  const dialog = ensureMonitorDialog();
  const title = dialog.querySelector("#monitorDialogTitle");
  title.textContent = isEdit ? "编辑规则" : "新建规则";

  const accountSel = dialog.querySelector("#monitorAccount");
  accountSel.innerHTML = accountOptions(monitor?.account_id);
  dialog.querySelector("#monitorName").value = monitor?.name || "";
  dialog.querySelector("#monitorChat").value = monitor?.chat_id || "";
  dialog.querySelector("#monitorKeyword").value = monitor?.contains_text || "";
  dialog.querySelector("#monitorEventType").value = monitor?.event_type || "message.created";
  dialog.querySelector("#monitorMessageType").value = monitor?.message_type || "";
  dialog.querySelector("#monitorAction").value = monitor?.action || "record";
  dialog.querySelector("#monitorText").value = String(monitor?.action_config?.text || monitor?.action_config?.body || "");
  dialog.querySelector("#monitorEnabled").checked = monitor ? Boolean(monitor.enabled) : true;
  syncMonitorTextVisibility(dialog);

  dialog.querySelector("#monitorAction").onchange = () => syncMonitorTextVisibility(dialog);
  accountSel.onchange = () => fieldError(dialog, "monitorAccount", "");

  const saveBtn = dialog.querySelector("#monitorSaveBtn");
  saveBtn.onclick = async () => {
    const accountId = accountSel.value;
    const action = dialog.querySelector("#monitorAction").value;
    const name = dialog.querySelector("#monitorName").value.trim();
    const text = dialog.querySelector("#monitorText").value;
    let failed = false;
    if (!accountId) {
      fieldError(dialog, "monitorAccount", "请选择要监控的微信账号");
      failed = true;
    }
    if (!name) {
      fieldError(dialog, "monitorName", "请填写规则名称");
      failed = true;
    }
    if (action === "send_text" && !text.trim()) {
      fieldError(dialog, "monitorText", "自动回复内容不能为空");
      failed = true;
    }
    if (failed) return;

    const account = findAccount(accountId);
    const payload = {
      ...(monitor ? { monitor_id: monitor.monitor_id } : {}),
      name,
      account_id: accountId,
      chat_id: dialog.querySelector("#monitorChat").value.trim(),
      event_type: dialog.querySelector("#monitorEventType").value,
      message_type: dialog.querySelector("#monitorMessageType").value,
      contains_text: dialog.querySelector("#monitorKeyword").value.trim(),
      action,
      action_config: action === "record" ? { text } : { text },
      enabled: dialog.querySelector("#monitorEnabled").checked,
    };
    if (action === "send_text") {
      // F7: pin the reply to the identity bound to this slot at creation time.
      if (!account || account.identity_binding_state !== "bound" || !account.wechat_identity_uuid) {
        fieldError(dialog, "monitorAccount", "该微信账号尚未绑定身份，自动回复会被拒绝执行；请先完成登录绑定。");
        return;
      }
      payload.expected_wechat_identity_uuid = account.wechat_identity_uuid;
    }
    saveBtn.disabled = true;
    await saveWithFeedback(
      () => api.saveAgentMonitor(payload),
      isEdit ? "规则已保存" : "规则已创建",
      reloadData,
      { dialog }
    );
    saveBtn.disabled = false;
  };

  dialog.querySelector("#monitorCancelBtn").onclick = () => closeDialog(dialog);
  openDialog(dialog);
}

function syncMonitorTextVisibility(dialog) {
  const action = dialog.querySelector("#monitorAction").value;
  const wrap = dialog.querySelector("#monitorTextWrap");
  const label = dialog.querySelector("#monitorTextLabel");
  const input = dialog.querySelector("#monitorText");
  if (action === "summary") {
    wrap.hidden = false;
    label.textContent = "总结附加要求（可选）";
    input.placeholder = "例如：重点提取待办事项";
  } else if (action === "send_text") {
    wrap.hidden = false;
    label.textContent = "自动回复内容";
    input.placeholder = "支持占位符，如：收到：{{message.text}}";
  } else {
    wrap.hidden = false;
    label.textContent = "记录内容（可选，默认记录消息原文）";
    input.placeholder = "支持占位符，如：{{message.author.display_name}}: {{message.text}}";
  }
}

function openScheduleDialog(schedule, reloadData) {
  const isEdit = Boolean(schedule);
  const dialog = ensureScheduleDialog();
  dialog.querySelector("#scheduleDialogTitle").textContent = isEdit ? "编辑定时任务" : "新建定时任务";

  const accountSel = dialog.querySelector("#scheduleAccount");
  accountSel.innerHTML = accountOptions(schedule?.account_id, { requireBound: false });
  dialog.querySelector("#scheduleName").value = schedule?.name || "";
  dialog.querySelector("#scheduleChat").value = schedule?.chat_id || "";
  dialog.querySelector("#scheduleTaskType").value = schedule?.task_type || "record";
  dialog.querySelector("#scheduleInterval").value = String(schedule?.interval_seconds || 3600);
  dialog.querySelector("#scheduleText").value = String(schedule?.payload?.text || schedule?.payload?.body || "");
  dialog.querySelector("#scheduleEnabled").checked = schedule ? Boolean(schedule.enabled) : true;
  syncScheduleTextVisibility(dialog);

  dialog.querySelector("#scheduleTaskType").onchange = () => syncScheduleTextVisibility(dialog);
  accountSel.onchange = () => fieldError(dialog, "scheduleAccount", "");

  const saveBtn = dialog.querySelector("#scheduleSaveBtn");
  saveBtn.onclick = async () => {
    const accountId = accountSel.value;
    const taskType = dialog.querySelector("#scheduleTaskType").value;
    const name = dialog.querySelector("#scheduleName").value.trim();
    const chatId = dialog.querySelector("#scheduleChat").value.trim();
    const text = dialog.querySelector("#scheduleText").value;
    let failed = false;
    if (!accountId) {
      fieldError(dialog, "scheduleAccount", "请选择执行任务的微信账号");
      failed = true;
    }
    if (!name) {
      fieldError(dialog, "scheduleName", "请填写任务名称");
      failed = true;
    }
    if (taskType !== "record" && !chatId) {
      fieldError(dialog, "scheduleChat", "定时发送和定时总结需要指定会话");
      failed = true;
    }
    if (taskType === "send_text" && !text.trim()) {
      fieldError(dialog, "scheduleText", "发送内容不能为空");
      failed = true;
    }
    if (failed) return;

    const account = findAccount(accountId);
    const payload = {
      ...(schedule ? { schedule_id: schedule.schedule_id } : {}),
      name,
      task_type: taskType,
      account_id: accountId,
      chat_id: chatId,
      interval_seconds: Number(dialog.querySelector("#scheduleInterval").value || 3600),
      // New tasks start from now; edits keep the stored schedule position.
      next_run_at: isEdit && schedule?.next_run_at ? schedule.next_run_at : new Date().toISOString(),
      payload: taskType === "record" ? { body: text } : { text, instruction: text },
      enabled: dialog.querySelector("#scheduleEnabled").checked,
    };
    if (account) {
      // F6: pin the job to the slot instance and its bound identity at
      // creation time; the Agent re-validates before every execution.
      payload.instance_uuid = account.instance_uuid || "";
      payload.expected_wechat_identity_uuid =
        account.identity_binding_state === "bound" ? account.wechat_identity_uuid || "" : "";
    }
    if (taskType === "send_text") {
      if (!account || account.identity_binding_state !== "bound" || !account.wechat_identity_uuid) {
        fieldError(dialog, "scheduleAccount", "该微信账号尚未绑定身份，无法创建定时发送；请先完成登录绑定。");
        return;
      }
    }
    saveBtn.disabled = true;
    await saveWithFeedback(
      () => api.saveAgentSchedule(payload),
      isEdit ? "定时任务已保存" : "定时任务已创建",
      reloadData,
      { dialog }
    );
    saveBtn.disabled = false;
  };

  dialog.querySelector("#scheduleCancelBtn").onclick = () => closeDialog(dialog);
  openDialog(dialog);
}

function syncScheduleTextVisibility(dialog) {
  const taskType = dialog.querySelector("#scheduleTaskType").value;
  const label = dialog.querySelector("#scheduleTextLabel");
  const input = dialog.querySelector("#scheduleText");
  if (taskType === "send_text") {
    label.textContent = "发送内容";
    input.placeholder = "到点后原样发送；支持 {{due_at}} 等占位符";
  } else if (taskType === "summary") {
    label.textContent = "总结附加要求（可选）";
    input.placeholder = "例如：重点提取待办事项";
  } else {
    label.textContent = "记录内容（可选）";
    input.placeholder = "到点后记录一条备忘";
  }
}

function openTemplateDialog(template, reloadData) {
  const isEdit = Boolean(template);
  const dialog = ensureTemplateDialog();
  dialog.querySelector("#templateDialogTitle").textContent = isEdit ? "编辑模板" : "新建模板";
  dialog.querySelector("#templateName").value = template?.name || "";
  dialog.querySelector("#templateBody").value = template?.body || "";
  dialog.querySelector("#templateEnabled").checked = template ? Boolean(template.enabled) : true;

  const saveBtn = dialog.querySelector("#templateSaveBtn");
  saveBtn.onclick = async () => {
    const name = dialog.querySelector("#templateName").value.trim();
    const body = dialog.querySelector("#templateBody").value;
    let failed = false;
    if (!name) {
      fieldError(dialog, "templateName", "请填写模板名称");
      failed = true;
    }
    if (!body.trim()) {
      fieldError(dialog, "templateBody", "模板内容不能为空");
      failed = true;
    }
    if (failed) return;
    saveBtn.disabled = true;
    await saveWithFeedback(
      () =>
        api.saveAgentTemplate({
          ...(template ? { template_id: template.template_id } : {}),
          name,
          body,
          enabled: dialog.querySelector("#templateEnabled").checked,
        }),
      isEdit ? "模板已保存" : "模板已创建",
      reloadData,
      { dialog }
    );
    saveBtn.disabled = false;
  };

  dialog.querySelector("#templateCancelBtn").onclick = () => closeDialog(dialog);
  openDialog(dialog);
}

/* ---------------------------------------------------------------- shared */

async function saveWithFeedback(action, successText, reloadData, { dialog = null } = {}) {
  try {
    await action();
    toast({ title: successText, tone: "good" });
    if (dialog) closeDialog(dialog);
    if (typeof reloadData === "function") await reloadData();
  } catch (err) {
    const message = err instanceof ApiError ? err.message : String(err);
    toast({ title: "操作失败", text: message, tone: "bad", duration: 6000 });
    if (!dialog) throw err;
  }
}

let runsDialogEl = null;
function ensureRunsDialog() {
  if (runsDialogEl) return runsDialogEl;
  runsDialogEl = document.createElement("dialog");
  runsDialogEl.id = "runsDialog";
  runsDialogEl.className = "modal";
  runsDialogEl.innerHTML = `
    <div class="modal-shell">
      <div class="modal-head">
        <div class="modal-head-text">
          <div class="modal-title" id="runsTitle">执行记录</div>
        </div>
        <button class="btn btn-icon" id="runsCloseBtn" aria-label="关闭">${icon("close")}</button>
      </div>
      <div class="modal-body" id="runsBody" style="max-height: 60vh; overflow: auto;"></div>
      <div class="modal-foot">
        <button class="btn btn-ghost" id="runsCancelBtn">关闭</button>
      </div>
    </div>
  `;
  document.body.appendChild(runsDialogEl);
  runsDialogEl.querySelector("#runsCloseBtn").onclick = () => closeDialog(runsDialogEl);
  runsDialogEl.querySelector("#runsCancelBtn").onclick = () => closeDialog(runsDialogEl);
  return runsDialogEl;
}

let monitorDialogEl = null;
function ensureMonitorDialog() {
  if (monitorDialogEl) return monitorDialogEl;
  monitorDialogEl = document.createElement("dialog");
  monitorDialogEl.id = "monitorDialog";
  monitorDialogEl.className = "modal";
  monitorDialogEl.innerHTML = `
    <div class="modal-shell">
      <div class="modal-head">
        <div class="modal-head-text">
          <div class="modal-title" id="monitorDialogTitle">新建规则</div>
        </div>
        <button class="btn btn-icon" id="monitorCloseBtn" aria-label="关闭">${icon("close")}</button>
      </div>
      <div class="modal-body">
        <div class="field">
          <label class="label" for="monitorName">规则名称</label>
          <input class="input" id="monitorName" placeholder="例如：部署关键词提醒" />
        </div>
        <div class="field">
          <label class="label" for="monitorAccount">监控账号（规则只对该微信身份生效）</label>
          <select class="select" id="monitorAccount"></select>
        </div>
        <div class="field">
          <label class="label" for="monitorChat">会话 ID（可选，留空匹配全部会话）</label>
          <input class="input mono" id="monitorChat" placeholder="例如：123456@chatroom" />
        </div>
        <div class="field">
          <label class="label" for="monitorKeyword">关键词（可选）</label>
          <input class="input" id="monitorKeyword" placeholder="消息包含该关键词才触发" />
        </div>
        <div class="field">
          <label class="label" for="monitorAction">触发后的动作</label>
          <select class="select" id="monitorAction">
            <option value="record">记录到备忘</option>
            <option value="send_text">自动回复发送文本</option>
            <option value="summary">AI 总结（需要 Agent 配置大模型）</option>
          </select>
        </div>
        <div class="field" id="monitorTextWrap">
          <label class="label" id="monitorTextLabel" for="monitorText">内容</label>
          <textarea class="textarea" id="monitorText" rows="3"></textarea>
        </div>
        <div class="field">
          <label class="label" for="monitorEventType">事件类型</label>
          <select class="select" id="monitorEventType">
            <option value="message.created">新消息</option>
            <option value="message.updated">消息更新</option>
          </select>
        </div>
        <div class="field">
          <label class="label" for="monitorMessageType">消息类型（可选）</label>
          <select class="select" id="monitorMessageType">
            <option value="">全部</option>
            <option value="text">文本</option>
            <option value="image">图片</option>
            <option value="file">文件</option>
            <option value="link">链接</option>
          </select>
        </div>
        <div class="field">
          <label class="switch">
            <input type="checkbox" id="monitorEnabled" checked aria-label="创建后立即启用" />
            <span class="switch-track"></span>
            <span>创建后立即启用</span>
          </label>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" id="monitorCancelBtn">取消</button>
        <button class="btn btn-primary" id="monitorSaveBtn">保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(monitorDialogEl);
  monitorDialogEl.querySelector("#monitorCloseBtn").onclick = () => closeDialog(monitorDialogEl);
  return monitorDialogEl;
}

let scheduleDialogEl = null;
function ensureScheduleDialog() {
  if (scheduleDialogEl) return scheduleDialogEl;
  scheduleDialogEl = document.createElement("dialog");
  scheduleDialogEl.id = "scheduleDialog";
  scheduleDialogEl.className = "modal";
  scheduleDialogEl.innerHTML = `
    <div class="modal-shell">
      <div class="modal-head">
        <div class="modal-head-text">
          <div class="modal-title" id="scheduleDialogTitle">新建定时任务</div>
        </div>
        <button class="btn btn-icon" id="scheduleCloseBtn" aria-label="关闭">${icon("close")}</button>
      </div>
      <div class="modal-body">
        <div class="field">
          <label class="label" for="scheduleName">任务名称</label>
          <input class="input" id="scheduleName" placeholder="例如：每日群汇总" />
        </div>
        <div class="field">
          <label class="label" for="scheduleAccount">执行账号</label>
          <select class="select" id="scheduleAccount"></select>
        </div>
        <div class="field">
          <label class="label" for="scheduleTaskType">任务类型</label>
          <select class="select" id="scheduleTaskType">
            <option value="record">定时记录</option>
            <option value="send_text">定时发送文本</option>
            <option value="summary">定时总结（需要 Agent 配置大模型）</option>
          </select>
        </div>
        <div class="field">
          <label class="label" for="scheduleChat">会话 ID（发送 / 总结必填）</label>
          <input class="input mono" id="scheduleChat" placeholder="例如：123456@chatroom" />
        </div>
        <div class="field">
          <label class="label" for="scheduleInterval">重复间隔</label>
          <select class="select" id="scheduleInterval">
            ${INTERVAL_OPTIONS.map((opt) => `<option value="${opt.value}">${escapeHtml(opt.label)}</option>`).join("")}
          </select>
        </div>
        <div class="field" id="scheduleTextWrap">
          <label class="label" id="scheduleTextLabel" for="scheduleText">内容</label>
          <textarea class="textarea" id="scheduleText" rows="3"></textarea>
        </div>
        <div class="field">
          <p class="field-hint">定时发送绑定创建时的微信身份；每次执行前会重新校验，身份变化时任务停止执行并记录失败。</p>
        </div>
        <div class="field">
          <label class="switch">
            <input type="checkbox" id="scheduleEnabled" checked aria-label="创建后立即启用" />
            <span class="switch-track"></span>
            <span>创建后立即启用</span>
          </label>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" id="scheduleCancelBtn">取消</button>
        <button class="btn btn-primary" id="scheduleSaveBtn">保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(scheduleDialogEl);
  scheduleDialogEl.querySelector("#scheduleCloseBtn").onclick = () => closeDialog(scheduleDialogEl);
  return scheduleDialogEl;
}

let templateDialogEl = null;
function ensureTemplateDialog() {
  if (templateDialogEl) return templateDialogEl;
  templateDialogEl = document.createElement("dialog");
  templateDialogEl.id = "templateDialog";
  templateDialogEl.className = "modal";
  templateDialogEl.innerHTML = `
    <div class="modal-shell">
      <div class="modal-head">
        <div class="modal-head-text">
          <div class="modal-title" id="templateDialogTitle">新建模板</div>
        </div>
        <button class="btn btn-icon" id="templateCloseBtn" aria-label="关闭">${icon("close")}</button>
      </div>
      <div class="modal-body">
        <div class="field">
          <label class="label" for="templateName">模板名称</label>
          <input class="input" id="templateName" placeholder="例如：会议提醒" />
        </div>
        <div class="field">
          <label class="label" for="templateBody">模板内容</label>
          <textarea class="textarea" id="templateBody" rows="4" placeholder="支持占位符，如：{{message.text}}"></textarea>
        </div>
        <div class="field">
          <label class="switch">
            <input type="checkbox" id="templateEnabled" checked aria-label="创建后立即启用" />
            <span class="switch-track"></span>
            <span>创建后立即启用</span>
          </label>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" id="templateCancelBtn">取消</button>
        <button class="btn btn-primary" id="templateSaveBtn">保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(templateDialogEl);
  templateDialogEl.querySelector("#templateCloseBtn").onclick = () => closeDialog(templateDialogEl);
  return templateDialogEl;
}
