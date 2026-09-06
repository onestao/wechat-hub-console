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
import { initial, fmtLastActivity, fmtDateTime, fmtRelative } from "./format.js";

/**
 * C2 — safe avatar source.  Only same-origin relative paths are used as-is;
 * absolute http(s) sources are always re-served through the Console avatar
 * proxy so the browser never has to trust an external URL.
 * @param {object} [wechatProfile]
 * @param {string} [identityUuid]
 * @returns {string}
 */
export function avatarSrcOf(wechatProfile = {}, identityUuid = "") {
  const raw = String(wechatProfile?.avatar_url || "").trim();
  if (!raw) return "";
  if (raw.startsWith("/") && !raw.startsWith("//")) return raw;
  if (/^https?:\/\//i.test(raw) && identityUuid) {
    return `/api/avatar/${encodeURIComponent(identityUuid)}`;
  }
  return "";
}

/**
 * C8 — switcher label: nickname — display_name, never a bare account_id.
 * @param {object} [account]
 * @returns {string}
 */
export function accountSwitchLabel(account) {
  const profile = account?.wechat_profile || {};
  const nickname = String(profile?.nickname || "").trim();
  const hubName = String(account?.display_name || "").trim();
  const parts = [];
  if (nickname && nickname !== hubName) parts.push(nickname);
  if (hubName) parts.push(hubName);
  return parts.join(" — ") || nickname || hubName || String(account?.account_id || "");
}

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
  const running = Boolean(runtimeAccount?.running ?? coreAccount?.runtime?.running);
  const provider = providerOf(runtimeAccount || coreAccount);
  const provLabel = providerLabel(runtimeAccount || coreAccount);
  const provTechnical = providerTechnical(runtimeAccount || coreAccount);
  const isLegacyDefault = Boolean(
    runtimeAccount?.legacy || (!runtimeAccount && coreAccount?.legacy)
  );

  // Identity v2 projection (contract §5.1).
  const wechatProfile =
    runtimeAccount?.wechat_profile || coreAccount?.wechat_profile || {};
  const identityBindingState = String(
    runtimeAccount?.identity_binding_state ||
      coreAccount?.identity_binding_state ||
      ""
  );
  const nickname = String(wechatProfile?.nickname || "").trim();
  const loggedInUser = String(
    runtimeAccount?.logged_in_user || coreAccount?.logged_in_user || ""
  ).trim();
  const wechatUserId = String(wechatProfile?.wechat_user_id || loggedInUser || "").trim();
  const instanceUuid = String(
    runtimeAccount?.instance_uuid || coreAccount?.instance_uuid || ""
  );
  const identityUuid = String(
    runtimeAccount?.wechat_identity_uuid || coreAccount?.wechat_identity_uuid || ""
  );
  const observedWxid = String(
    runtimeAccount?.observed_wechat_user_id ||
      coreAccount?.observed_wechat_user_id ||
      ""
  ).trim();

  // C1 fallback chain: nickname → logged_in_user(wxid) → display_name.
  const wechatName = nickname || loggedInUser || "";
  const showHubName = Boolean(wechatName) && wechatName !== name;
  const displayName = wechatName || name;
  const initialGlyph = initial(displayName, "微");
  const avatarSrc = avatarSrcOf(wechatProfile, identityUuid);
  const lastSyncText = coreAccount?.sync?.last_event_at
    ? fmtRelative(coreAccount.sync.last_event_at)
    : "";

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

  // C6 — identity mismatch is the highest-priority state: normal primary
  // actions are blocked until the operator resolves the conflict.
  const identityMismatch = identityBindingState === "mismatch";
  if (identityMismatch) {
    tone = "bad";
    statusText = "已暂停：登录了另一个微信";
    hint = "为避免历史消息混在一起，消息同步和发送已暂停";
    primaryAction = { id: "identity", label: "处理身份冲突", variant: "primary" };
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
  menu.push({ action: "rename", label: "修改名称", icon: "edit" });
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
    instanceUuid: instanceUuid || "--",
    runtimeAlias: String(
      runtimeAccount?.runtime_alias || coreAccount?.runtime_alias || accountId
    ),
    resourceKey: String(
      runtimeAccount?.resource_key || coreAccount?.resource_key || "--"
    ),
    wechatIdentityUuid: identityUuid || "--",
    wechatUserId: wechatUserId || "--",
    identityBindingState: identityBindingState || "--",
    containerId: String(
      runtimeAccount?.container_id || coreAccount?.runtime?.container_id || "--"
    ),
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
    // C1 — real WeChat identity first, Hub display_name clearly secondary.
    displayName,
    wechatName,
    hubName: name,
    showHubName,
    nickname,
    wechatUserId,
    identityBindingState,
    identityMismatch,
    mismatchInfo: {
      boundNickname: nickname,
      boundWxid: wechatUserId,
      observedWxid,
    },
    avatarSrc,
    initial: initialGlyph,
    lastSyncText,
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
