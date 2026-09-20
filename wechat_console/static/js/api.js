/* Single fetch boundary for the Console UI.
 *
 * Everything the views need goes through here so provider-specific and
 * security-relevant request shapes stay in one place.
 */

export class ApiError extends Error {
  constructor(message, { code = "http_error", status = 0, details = {} } = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function request(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (cause) {
    throw new ApiError("无法连接 WeChat Hub Console 服务", {
      code: "network_error",
      details: { cause: String(cause) },
    });
  }
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_) {
      payload = { error: { message: text } };
    }
  }
  if (!response.ok) {
    const error = payload?.error || {};
    throw new ApiError(error.message || error || `HTTP ${response.status}`, {
      code: error.code || "http_error",
      status: response.status,
      details: error.details || {},
    });
  }
  return payload;
}

function post(url, body = {}, headers = {}) {
  return request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

const enc = encodeURIComponent;

export const api = {
  status: () => request("/api/status"),
  syncEvents: () => post("/api/events/sync"),

  runtimeAccounts: () => request("/api/runtime/accounts"),
  createAccount: (payload) => post("/api/runtime/accounts", payload),
  accountDetail: (accountId) => request(`/api/runtime/accounts/${enc(accountId)}`),
  updateAccount: (accountId, payload) =>
    post(`/api/runtime/accounts/${enc(accountId)}/update`, payload),
  confirmSwitch: (accountId, payload = {}) =>
    post(`/api/runtime/accounts/${enc(accountId)}/confirm-switch`, payload),
  accountAction: (accountId, action) => post(`/api/runtime/accounts/${enc(accountId)}/${action}`),
  removeAccount: (accountId) =>
    request(`/api/runtime/accounts/${enc(accountId)}`, { method: "DELETE" }),

  /** C2 — avatars are always served same-origin via the Console proxy. */
  avatarUrl: (identityUuid) => `/api/avatar/${enc(identityUuid)}`,

  startLogin: (accountId) => post(`/api/runtime/accounts/${enc(accountId)}/login`),
  loginStatus: (accountId) => request(`/api/runtime/accounts/${enc(accountId)}/login`),
  /** Cache-busted so a stale snapshot can never be shown; never persisted. */
  loginSnapshotUrl: (accountId) =>
    `/api/runtime/accounts/${enc(accountId)}/login/snapshot?t=${Date.now()}`,
  desktop: (accountId) => request(`/api/runtime/accounts/${enc(accountId)}/desktop`),

  chats: (accountId, query = "") =>
    request(`/api/chats?account_id=${enc(accountId)}&query=${enc(query)}`),
  messages: (params) => request(`/api/messages?${new URLSearchParams(params).toString()}`),
  mediaUrl: (mediaId, accountId) => `/api/media/${enc(mediaId)}?account_id=${enc(accountId)}`,
  contacts: (params = {}) => request(`/api/contacts?${new URLSearchParams(params).toString()}`),
  groupMembers: (chatId, params = {}) =>
    request(`/api/chats/${enc(chatId)}/members?${new URLSearchParams(params).toString()}`),
  identityProfile: (params = {}) =>
    request(`/api/identity/profile?${new URLSearchParams(params).toString()}`),
  avatarUrl: (avatarKey) => `/api/avatar/${enc(avatarKey)}`,

  sendText: (payload, idempotencyKey) =>
    post("/api/send/text", payload, { "Idempotency-Key": idempotencyKey }),
  sendImage: (payload, idempotencyKey) =>
    post("/api/send/image", payload, { "Idempotency-Key": idempotencyKey }),
  sendFile: (payload, idempotencyKey) =>
    post("/api/send/file", payload, { "Idempotency-Key": idempotencyKey }),
  sendStatus: (sendId) => request(`/api/sends/${enc(sendId)}`),

  saved: (params = {}) => request(`/api/saved?${new URLSearchParams(params).toString()}`),
  saveMessage: (payload) => post("/api/saved", payload),
  updateSaved: (savedId, payload) => post(`/api/saved/${enc(savedId)}`, payload),
  archiveSaved: (savedId) => post(`/api/saved/${enc(savedId)}/archive`),
  deleteSaved: (savedId) => request(`/api/saved/${enc(savedId)}`, { method: "DELETE" }),
  savedMediaUrl: (savedMediaId) => `/api/saved-media/${enc(savedMediaId)}`,

  // Optional WeChat Agent automation surface (proxied to the Agent service).
  agentStatus: () => request("/api/agent/status"),
  agentMonitors: () => request("/api/agent/monitors"),
  saveAgentMonitor: (payload) => post("/api/agent/monitors", payload),
  deleteAgentMonitor: (monitorId) => request(`/api/agent/monitors/${enc(monitorId)}`, { method: "DELETE" }),
  agentMonitorRuns: (monitorId) => request(`/api/agent/monitors/${enc(monitorId)}/runs`),
  agentSchedules: () => request("/api/agent/schedules"),
  saveAgentSchedule: (payload) => post("/api/agent/schedules", payload),
  deleteAgentSchedule: (scheduleId) => request(`/api/agent/schedules/${enc(scheduleId)}`, { method: "DELETE" }),
  agentScheduleRuns: (scheduleId) => request(`/api/agent/schedules/${enc(scheduleId)}/runs`),
  agentTemplates: () => request("/api/agent/templates"),
  saveAgentTemplate: (payload) => post("/api/agent/templates", payload),
  deleteAgentTemplate: (templateId) => request(`/api/agent/templates/${enc(templateId)}`, { method: "DELETE" }),

  logs: (params = {}) => request(`/api/logs?${new URLSearchParams(params).toString()}`),

  // Consumer Control (Disabled / EFB / Agent).  The Runtime owns the container
  // lifecycle; Console only ever talks to Core, never to the Docker socket.
  installStatus: () => request("/api/install/status"),
  consumers: () => request("/api/consumers"),
  setConsumerMode: (mode) => post("/api/consumers/mode", { mode }),
  startConsumer: (consumer) => post(`/api/consumers/${enc(consumer)}/start`, {}),
  stopConsumer: (consumer) => post(`/api/consumers/${enc(consumer)}/stop`, {}),
};
