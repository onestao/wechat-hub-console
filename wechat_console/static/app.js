/* WeChat Hub Console — Application Bootstrap & Main Controller (v2).
 *
 * Integrates Router, State Store, Global Navigation, and Page Views.
 */

import { mountIconSprite } from "./js/icons.js";
import { api } from "./js/api.js";
import { state, setState } from "./js/state.js";
import { initRouter, parseRoute } from "./js/router.js";
import { accountViewModel } from "./js/account-view-model.js";

import { renderHomeView } from "./js/views/home.js";
import { renderAccountsView } from "./js/views/accounts.js";
import { renderMessagesView } from "./js/views/messages.js";
import { renderSavedView } from "./js/views/saved.js";
import { renderAutomationView } from "./js/views/automation.js";
import { renderSettingsView } from "./js/views/settings.js";
import { renderContactsView } from "./js/views/contacts.js";

const PAGE_TITLES = {
  home: "首页",
  accounts: "微信",
  messages: "消息",
  contacts: "通讯录",
  saved: "收藏",
  automation: "自动化",
  settings: "设置",
};

let currentRouteInfo = { route: "home", primary: "home", sub: "" };

let loadAllDataSeq = 0;

async function loadAllData() {
  const requestSeq = ++loadAllDataSeq;
  const requestedAccountId = state.activeAccountId || "";

  try {
    const status = await api.status();
    if (requestSeq !== loadAllDataSeq) {
      return;
    }

    const core = status.core || {};
    const runtimeMgmt = status.runtime_management || {};
    const coreAccounts = status.accounts || [];
    const runtimeAccounts = runtimeMgmt.accounts || [];

    // All available account IDs in the returned status
    const allIds = Array.from(
      new Set([
        ...runtimeAccounts.map((a) => a.account_id),
        ...coreAccounts.map((a) => a.account_id),
      ])
    );

    // Derive effective owner:
    // Prioritize caller intent if valid in status; fallback to active or first available
    let effectiveAccountId = requestedAccountId;
    if (!effectiveAccountId || !allIds.includes(effectiveAccountId)) {
      if (state.activeAccountId && allIds.includes(state.activeAccountId)) {
        effectiveAccountId = state.activeAccountId;
      } else {
        effectiveAccountId = allIds[0] || "";
      }
    }

    // Scoped secondary data fetch using effective owner
    const isHomeRoute = currentRouteInfo.primary === "home";
    const [chatsRes, messagesRes, savedRes] = await Promise.allSettled([
      effectiveAccountId ? api.chats(effectiveAccountId) : Promise.resolve({ chats: [] }),
      isHomeRoute
        ? api.messages({ account_id: effectiveAccountId, limit: 5 })
        : Promise.resolve({ messages: [] }),
      api.saved({ limit: 100 }),
    ]);

    // Generation stale guard after secondary requests
    if (requestSeq !== loadAllDataSeq) {
      return;
    }

    // Ownership compatibility check:
    // Drop if user actively switched to another account while request was in-flight.
    // If the requested account vanished from latest status, allow committing effective fallback.
    const currentIntent = state.activeAccountId || "";
    const userChangedIntent =
      currentIntent &&
      currentIntent !== requestedAccountId &&
      currentIntent !== effectiveAccountId;

    if (userChangedIntent) {
      return;
    }

    const nextChats = chatsRes.status === "fulfilled" ? chatsRes.value?.chats || [] : [];
    const nextMessages = messagesRes.status === "fulfilled" ? messagesRes.value?.messages || [] : [];
    const nextSaved = savedRes.status === "fulfilled" ? (savedRes.value.items || savedRes.value.saved_messages || []) : [];

    // Selection reconciliation for selectedChatId
    let nextSelectedChatId = state.selectedChatId;
    if (nextSelectedChatId && !nextChats.some((c) => c.chat_id === nextSelectedChatId)) {
      nextSelectedChatId = "";
    }

    // Atomic commit
    setState({
      status,
      coreOk: Boolean(core.ok),
      runtimeManagement: runtimeMgmt,
      accounts: coreAccounts,
      runtimeAccounts,
      activeAccountId: effectiveAccountId,
      selectedChatId: nextSelectedChatId,
      chats: nextChats,
      messages: nextMessages,
      saved: nextSaved,
    });
  } catch (err) {
    if (requestSeq !== loadAllDataSeq) {
      return;
    }
    console.warn("loadAllData encountered error:", err);
    setState({ coreOk: false });
  }

  if (requestSeq !== loadAllDataSeq) {
    return;
  }

  updateGlobalIndicators();
  renderCurrentPage();
}

function updateGlobalIndicators() {
  const coreOk = state.coreOk !== false && state.status?.core?.ok !== false;

  // Nav Core state bar
  const coreStateEl = document.getElementById("navCoreState");
  const coreStateText = document.getElementById("navCoreStateText");
  if (coreStateEl && coreStateText) {
    if (coreOk) {
      coreStateEl.dataset.tone = "";
      coreStateText.textContent = "运行正常";
    } else {
      coreStateEl.dataset.tone = "bad";
      coreStateText.textContent = "无法连接 WeChat Hub";
    }
  }

  // Compute attention accounts count for badge & dot
  const vms = (state.runtimeAccounts || []).map((ra) => {
    const ca = (state.accounts || []).find((a) => a.account_id === ra.account_id);
    return accountViewModel(ra, ca, { runtimeManagement: state.runtimeManagement, coreOk });
  });

  const attentionCount = vms.filter((v) => v.tone === "bad" || v.tone === "warn").length;

  const navBadge = document.getElementById("navAccountBadge");
  if (navBadge) {
    if (attentionCount > 0) {
      navBadge.style.display = "inline-block";
      navBadge.textContent = String(attentionCount);
    } else {
      navBadge.style.display = "none";
    }
  }

  const tabDot = document.getElementById("tabbarAccountDot");
  if (tabDot) {
    tabDot.style.display = attentionCount > 0 ? "block" : "none";
  }
}

function onRouteChanged(routeInfo) {
  currentRouteInfo = routeInfo;
  document.body.classList.remove("nav-open");

  const primary = routeInfo.primary;

  // Update Desktop Nav Active
  document.querySelectorAll(".nav-item").forEach((item) => {
    if (item.dataset.nav === primary) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });

  // Update Mobile Tabbar Active
  document.querySelectorAll(".tabbar-item").forEach((item) => {
    if (item.dataset.tab === primary) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });

  // Update Topbar Title
  const topbarTitle = document.getElementById("topbarTitle");
  if (topbarTitle) {
    topbarTitle.textContent = PAGE_TITLES[primary] || "WeChat Hub";
  }

  // Show / Hide Section Pages
  document.querySelectorAll(".page").forEach((page) => {
    const match = page.dataset.route === primary;
    page.hidden = !match;
  });

  renderCurrentPage();
}

function renderCurrentPage() {
  const primary = currentRouteInfo.primary;
  const sub = currentRouteInfo.sub;
  const pageEl = document.getElementById(`page-${primary}`);
  if (!pageEl) return;

  switch (primary) {
    case "home":
      renderHomeView(pageEl, loadAllData);
      break;
    case "accounts":
      renderAccountsView(pageEl, loadAllData);
      break;
    case "messages":
      renderMessagesView(pageEl, loadAllData);
      break;
    case "contacts":
      renderContactsView(pageEl, loadAllData);
      break;
    case "saved":
      renderSavedView(pageEl, loadAllData);
      break;
    case "automation":
      renderAutomationView(pageEl, loadAllData);
      break;
    case "settings":
      renderSettingsView(pageEl, loadAllData, sub);
      break;
  }
}

function initAppShell() {
  // Mount SVG Sprite
  mountIconSprite(document);

  // Hamburger Menu & Scrim
  const hamburgerBtn = document.getElementById("topbarHamburgerBtn");
  const scrim = document.getElementById("navScrim");

  if (hamburgerBtn) {
    hamburgerBtn.onclick = () => {
      document.body.classList.toggle("nav-open");
    };
  }

  if (scrim) {
    scrim.onclick = () => {
      document.body.classList.remove("nav-open");
    };
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("nav-open")) {
      document.body.classList.remove("nav-open");
    }
  });

  // Router Init
  initRouter(onRouteChanged);

  // Initial Data Load
  loadAllData();

  // Real-time Core Event Long-Polling Loop
  let eventCursor = "";
  let isPollingEvents = false;

  async function pollLoop() {
    if (isPollingEvents) return;
    isPollingEvents = true;
    while (true) {
      if (document.visibilityState !== "visible") {
        await new Promise((r) => setTimeout(r, 2000));
        continue;
      }
      try {
        const res = await api.pollEvents({ since: eventCursor, timeout: 20 });
        if (res && res.cursor) {
          eventCursor = res.cursor;
        }
        const events = (res && res.events) || [];
        if (events.length > 0) {
          const hasBusinessEvents = events.some((e) =>
            [
              "message.created",
              "message.updated",
              "send.updated",
              "chat.updated",
              "account.status",
              "identity.binding_changed",
            ].includes(e.event_type)
          );
          if (hasBusinessEvents) {
            loadAllData();
          }
        }
      } catch (err) {
        // Backoff slightly on transient error
        await new Promise((r) => setTimeout(r, 4000));
      }
    }
  }

  pollLoop();

  // 30s background poll safety fallback
  setInterval(() => {
    if (state.autoRefresh !== false) {
      loadAllData();
    }
  }, 30000);

  // Visibility Change Auto Refresh
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.autoRefresh !== false) {
      loadAllData();
    }
  });
}

// Boot when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initAppShell);
} else {
  initAppShell();
}

export { loadAllData, loadAllDataSeq };
