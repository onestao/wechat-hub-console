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

const PAGE_TITLES = {
  home: "首页",
  accounts: "微信",
  messages: "消息",
  saved: "收藏",
  automation: "自动化",
  settings: "设置",
};

let currentRouteInfo = { route: "home", primary: "home", sub: "" };

async function loadAllData() {
  try {
    const status = await api.status();
    const core = status.core || {};
    const runtimeMgmt = status.runtime_management || {};
    const coreAccounts = status.accounts || [];
    const runtimeAccounts = runtimeMgmt.accounts || [];

    setState({
      status,
      coreOk: Boolean(core.ok),
      runtimeManagement: runtimeMgmt,
      accounts: coreAccounts,
      runtimeAccounts,
    });

    // If activeAccountId is unset or no longer exists, pick the first available
    const allIds = Array.from(
      new Set([
        ...runtimeAccounts.map((a) => a.account_id),
        ...coreAccounts.map((a) => a.account_id),
      ])
    );
    if (!state.activeAccountId || !allIds.includes(state.activeAccountId)) {
      setState({ activeAccountId: allIds[0] || "" });
    }

    // Parallel fetch secondary data
    const [chatsRes, messagesRes, savedRes] = await Promise.allSettled([
      state.activeAccountId ? api.chats(state.activeAccountId) : Promise.resolve({ chats: [] }),
      api.messages({ limit: 100 }),
      api.saved({ limit: 100 }),
    ]);

    setState({
      chats: chatsRes.status === "fulfilled" ? chatsRes.value.chats || [] : [],
      messages: messagesRes.status === "fulfilled" ? messagesRes.value.messages || [] : [],
      saved: savedRes.status === "fulfilled" ? (savedRes.value.items || savedRes.value.saved_messages || []) : [],
    });
  } catch (err) {
    console.warn("loadAllData encountered error:", err);
    setState({ coreOk: false });
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

  // 30s background poll
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
