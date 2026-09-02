/* Account capability view model.
 *
 * Sender capability is per account in Core (`runtime.sender_capabilities`); the
 * top-level Core capability block stays Legacy-safe for old consumers.  The UI
 * must therefore ask the account, and only fall back to the Core-wide value
 * when an older Core does not report per-account capabilities at all.
 */

const PROVIDER_LABELS = {
  agent_wechat: "推荐模式（Beta）",
  legacy: "兼容模式",
};

const PROVIDER_TECHNICAL = {
  agent_wechat: "AgentWechat",
  legacy: "Legacy",
};

export function providerOf(account) {
  const raw = String(
    account?.runtime_provider || account?.runtime?.runtime_provider || "legacy",
  ).toLowerCase();
  return raw === "agent_wechat" ? "agent_wechat" : "legacy";
}

export function providerLabel(account) {
  return PROVIDER_LABELS[providerOf(account)];
}

export function providerTechnical(account) {
  return PROVIDER_TECHNICAL[providerOf(account)];
}

/** Derive what the UI is allowed to offer for one account. */
export function capabilitiesOf(account, { coreCapabilities = null } = {}) {
  const runtime = account?.runtime || {};
  const provider = providerOf(account);
  const perAccount = runtime.sender_capabilities;
  const caps = perAccount && typeof perAccount === "object" ? perAccount : coreCapabilities || {};
  const running = Boolean(account?.running ?? runtime.running);
  const agentHealthy = provider !== "agent_wechat" || account?.agent_server_healthy !== false;

  return {
    canSendText: Boolean(caps.text),
    canSendImage: Boolean(caps.image),
    canSendFile: Boolean(caps.file),
    canOpenDesktop: running && agentHealthy,
    canLogin: running && agentHealthy,
    canRestart: true,
    provider,
    providerLabel: PROVIDER_LABELS[provider],
    providerTechnical: PROVIDER_TECHNICAL[provider],
    /** Short reason shown when sending is unavailable — no engineering jargon. */
    sendDisabledReason: caps.text
      ? ""
      : provider === "legacy"
        ? "这个微信当前不支持从 Console 发送消息。"
        : "这个微信暂时不能发送消息。",
  };
}

/** Capability summary for the advanced drawer / diagnostics only. */
export function capabilitySummary(account) {
  const caps = account?.runtime?.sender_capabilities;
  if (!caps || typeof caps !== "object") return "--";
  return ["text", "image", "file"].map((key) => `${key}=${caps[key] ? "true" : "false"}`).join(" ");
}
