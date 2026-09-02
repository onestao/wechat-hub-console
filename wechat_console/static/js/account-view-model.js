/* Account View Model.
 *
 * The single canonical mapping from backend state to user-facing copy,
 * tones, primary actions, and menu definitions.
 */

import {
  providerOf,
  providerLabel,
  providerTechnical,
  capabilitiesOf,
  capabilitySummary,
} from "./capabilities.js";
import { initial, fmtLastActivity, fmtDateTime } from "./format.js";

/**
 * Build presentation view model for an account.
 * @param {object} runtimeAccount
 * @param {object} coreAccount
 * @param {object} [context]
 * @returns {object}
 */
export function accountViewModel(runtimeAccount, coreAccount, context = {}) {
  const accountId = runtimeAccount?.account_id || coreAccount?.account_id || "";
  const name = runtimeAccount?.display_name || coreAccount?.display_name || accountId;
  const initialGlyph = initial(name, "微");
  const running = Boolean(runtimeAccount?.running ?? coreAccount?.runtime?.running);
  const provider = providerOf(runtimeAccount || coreAccount);
  const provLabel = providerLabel(runtimeAccount || coreAccount);
  const provTechnical = providerTechnical(runtimeAccount || coreAccount);
  const isLegacyDefault = Boolean(
    runtimeAccount?.legacy || (!runtimeAccount && coreAccount?.legacy)
  );

  const caps = capabilitiesOf(runtimeAccount || coreAccount, {
    coreCapabilities: coreAccount?.runtime?.sender_capabilities,
  });

  const agentHealthy =
    provider !== "agent_wechat" || runtimeAccount?.agent_server_healthy !== false;

  let tone = "idle";
  let statusText = "已停止";
  let hint = "";
  let primaryAction = { id: "start", label: "启动", variant: "secondary" };

  const loginFlowState = runtimeAccount?.login_flow_state || "";
  const hasLoginError =
    loginFlowState === "error" ||
    loginFlowState === "timeout" ||
    Boolean(runtimeAccount?.login_flow_error);
  const isWaitingScan =
    coreAccount?.state === "login_required" ||
    Boolean(runtimeAccount?.snapshot_available) ||
    (Array.isArray(runtimeAccount?.windows) && runtimeAccount.windows.length > 0) ||
    loginFlowState === "waiting_for_scan" ||
    loginFlowState === "phone_confirm";

  if (!agentHealthy) {
    tone = "bad";
    statusText = "微信服务异常";
    hint = "微信进程仍在运行，控制服务暂时不可用";
    primaryAction = { id: "restart", label: "重新启动", variant: "secondary" };
  } else if (coreAccount?.state === "online") {
    tone = "good";
    statusText = "已连接";
    const lastWhen = fmtLastActivity(
      coreAccount?.last_seen_at ||
        coreAccount?.logged_in_at ||
        runtimeAccount?.started_at,
      "登录"
    );
    hint = lastWhen || "运行正常";
    primaryAction = { id: "open", label: "打开微信", variant: "secondary" };
  } else if (running && hasLoginError) {
    tone = "warn";
    statusText = "登录窗口暂时不可用";
    hint = "微信可能仍在启动，或者登录流程已经超时";
    primaryAction = { id: "relogin", label: "重新登录", variant: "primary" };
  } else if (running && isWaitingScan) {
    tone = "warn";
    statusText = "等待登录";
    hint = "微信已启动，等待扫码";
    primaryAction = { id: "login", label: "扫码登录", variant: "primary" };
  } else if (running) {
    tone = "busy";
    statusText = "正在启动";
    hint = "微信正在准备中…";
    primaryAction = {
      id: "waiting",
      label: "正在启动",
      variant: "secondary",
      disabled: true,
    };
  } else {
    tone = "idle";
    statusText = "已停止";
    hint =
      runtimeAccount?.autostart === false
        ? "自动启动已关闭"
        : "点击启动开始运行";
    primaryAction = { id: "start", label: "启动", variant: "secondary" };
  }

  // Build action menu
  const menu = [];
  if (running) {
    menu.push({ action: "restart", label: "重新启动", icon: "refresh" });
    menu.push({ action: "stop", label: "停止运行", icon: "stop" });
    menu.push({ action: "relogin", label: "重新登录", icon: "qr" });
  } else {
    menu.push({ action: "start", label: "启动", icon: "play" });
  }
  menu.push({ action: "advanced", label: "高级信息", icon: "info" });
  menu.push({ divider: true });
  menu.push({
    action: "remove",
    label: "移除微信",
    icon: "trash",
    tone: "danger",
    disabled: isLegacyDefault,
    disabledReason: isLegacyDefault ? "兼容模式默认微信不可移除" : "",
  });

  const advanced = {
    accountId,
    name,
    runtimeProvider: provTechnical,
    providerLabel: provLabel,
    coreState: coreAccount?.state || "等待热加载",
    runtimeHealth:
      runtimeAccount?.runtime_health || (running ? "running" : "stopped"),
    agentServerHealthy:
      runtimeAccount?.agent_server_healthy !== undefined
        ? String(runtimeAccount.agent_server_healthy)
        : "--",
    pid: Array.isArray(runtimeAccount?.pids)
      ? runtimeAccount.pids.join(", ")
      : String(runtimeAccount?.pid ?? "--"),
    uid: String(runtimeAccount?.uid ?? "--"),
    display: String(runtimeAccount?.display ?? "--"),
    home: String(runtimeAccount?.home ?? "--"),
    image: String(runtimeAccount?.current_image || runtimeAccount?.image || "--"),
    autostart: runtimeAccount?.autostart ? "是" : "否",
    senderCapability: capabilitySummary(coreAccount || runtimeAccount),
    windows: Array.isArray(runtimeAccount?.windows)
      ? String(runtimeAccount.windows.length)
      : String(runtimeAccount?.window_count ?? "--"),
    lastEventAt: fmtDateTime(coreAccount?.sync?.last_event_at),
  };

  return {
    accountId,
    name,
    initial: initialGlyph,
    tone,
    statusText,
    hint,
    running,
    provider,
    providerLabel: provLabel,
    providerTechnical: provTechnical,
    isLegacyDefault,
    primaryAction,
    menu,
    capabilities: caps,
    advanced,
  };
}
