/* Application State & Reactive Subscriptions.
 *
 * Minimal uni-directional state container.
 */

export const state = {
  // Global & Connectivity
  status: null,
  coreOk: true,
  runtimeManagement: null,
  autoRefresh: true,

  // Accounts
  accounts: [],
  runtimeAccounts: [],
  activeAccountId: "",

  // Messages / Chat view
  chats: [],
  selectedChatId: "",
  messages: [],
  selectedMessageType: "",
  messageQuery: "",

  // Send status tracking
  sendResult: null,

  // Saved messages
  saved: [],
  selectedSavedId: "",
  savedFilter: "all", // "all" | "image" | "file" | "link" | "annotated"
  savedQuery: "",

  // Diagnostics & Logs
  logs: [],
  logLevel: "",
  logCategory: "",
  logQuery: "",
};

const listeners = new Set();

/**
 * Update state with a patch and notify subscribers.
 * @param {Partial<typeof state>} patch
 */
export function setState(patch) {
  Object.assign(state, patch);
  for (const fn of listeners) {
    try {
      fn(state);
    } catch (err) {
      console.error("State listener error:", err);
    }
  }
}

/**
 * Subscribe to state changes.
 * @param {(s: typeof state) => void} fn
 * @returns {() => void} unsubscribe function
 */
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
