/* Automation View.
 *
 * Capability-oriented view for WeChat Agent features.
 */

import { state } from "../state.js";
import { navigate } from "../router.js";
import { icon } from "../icons.js";

/**
 * Render Automation View.
 * @param {HTMLElement} container
 * @param {() => Promise<void>} reloadData
 */
export function renderAutomationView(container, reloadData) {
  const agentIntegration = state.status?.integrations?.agent || {};
  const isAgentOk = Boolean(agentIntegration.configured && agentIntegration.ok);

  let heroHtml = "";
  if (isAgentOk) {
    heroHtml = `
      <div class="surface" style="padding: 24px;">
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;">
          <div style="display: flex; align-items: center; gap: 12px;">
            <div class="avatar" data-tone="good">${icon("sparkle")}</div>
            <div>
              <div class="item-title">WeChat Agent 已连接</div>
              <p class="caption" style="margin-top: 2px;">自动化服务正常运行中，可在后台编排规则与定时任务。</p>
            </div>
          </div>
          <span class="pill" data-tone="brand">运行正常</span>
        </div>
      </div>
    `;
  } else {
    heroHtml = `
      <div class="surface">
        <div class="empty">
          <div class="empty-icon">${icon("automation")}</div>
          <div class="empty-title">启用自动化功能</div>
          <p class="empty-text">启动 WeChat Agent 后，可以创建自动回复、关键词关注和定时任务。</p>
          <button class="btn btn-secondary" id="autoHowToBtn">查看如何启用</button>
        </div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="page-inner">
      <div class="page-head">
        <div>
          <div class="page-title">自动化</div>
          <p class="page-subtitle">自动回复、消息关注、定时任务和 AI 助手。</p>
        </div>
      </div>

      ${heroHtml}

      <div class="section">
        <div class="section-head">
          <div class="section-head-text">
            <h2 class="section-title">启用后可以做什么</h2>
          </div>
        </div>
        <div class="surface surface-flush">
          <div class="rows">
            <div class="row">
              <div class="avatar avatar-sm">${icon("message", { size: "sm" })}</div>
              <div class="row-body">
                <div class="row-title"><strong>自动回复</strong></div>
                <div class="row-meta">按关键词或会话自动回复消息。</div>
              </div>
            </div>
            <div class="row">
              <div class="avatar avatar-sm">${icon("bell", { size: "sm" })}</div>
              <div class="row-body">
                <div class="row-title"><strong>消息关注</strong></div>
                <div class="row-meta">关注重要联系人或关键词，出现时提醒。</div>
              </div>
            </div>
            <div class="row">
              <div class="avatar avatar-sm">${icon("clock", { size: "sm" })}</div>
              <div class="row-body">
                <div class="row-title"><strong>定时任务</strong></div>
                <div class="row-meta">定时发送问候、汇总或提醒消息。</div>
              </div>
            </div>
            <div class="row">
              <div class="avatar avatar-sm">${icon("sparkle", { size: "sm" })}</div>
              <div class="row-body">
                <div class="row-title"><strong>AI 助手</strong></div>
                <div class="row-meta">基于大模型的内容总结、问答与智能回复。</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  const howToBtn = container.querySelector("#autoHowToBtn");
  if (howToBtn) {
    howToBtn.onclick = () => {
      navigate("settings/advanced");
    };
  }
}
