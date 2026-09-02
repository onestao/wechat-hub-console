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
  accountAction: (accountId, action) => post(`/api/runtime/accounts/${enc(accountId)}/${action}`),
  removeAccount: (accountId) =>
    request(`/api/runtime/accounts/${enc(accountId)}`, { method: "DELETE" }),

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

  logs: (params = {}) => request(`/api/logs?${new URLSearchParams(params).toString()}`),
};
