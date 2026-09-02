/* Hash Router with legacy query param compatibility.
 *
 * Routes:
 *   #/home
 *   #/accounts
 *   #/messages
 *   #/saved
 *   #/automation
 *   #/settings
 *   #/settings/advanced
 */

const LEGACY_VIEW_MAP = {
  overview: "home",
  accounts: "accounts",
  chat: "messages",
  saved: "saved",
  services: "settings/advanced",
  agent: "automation",
  logs: "settings/advanced",
};

const VALID_ROUTES = new Set([
  "home",
  "accounts",
  "messages",
  "saved",
  "automation",
  "settings",
  "settings/advanced",
]);

const listeners = new Set();

export function parseRoute() {
  // 1. Check legacy query param ?view=...
  const params = new URLSearchParams(window.location.search);
  const legacyView = params.get("view");
  if (legacyView && LEGACY_VIEW_MAP[legacyView]) {
    const mapped = LEGACY_VIEW_MAP[legacyView];
    // Clean URL query without reload
    params.delete("view");
    const newSearch = params.toString() ? `?${params.toString()}` : "";
    const newUrl = `${window.location.pathname}${newSearch}#/${mapped}`;
    window.history.replaceState(null, "", newUrl);
    return { route: mapped, primary: mapped.split("/")[0], sub: mapped.split("/")[1] || "" };
  }

  // 2. Read hash
  const hash = window.location.hash.replace(/^#\/?/, "");
  const cleaned = hash.split("?")[0].replace(/\/$/, "");
  if (VALID_ROUTES.has(cleaned)) {
    return { route: cleaned, primary: cleaned.split("/")[0], sub: cleaned.split("/")[1] || "" };
  }

  // Default fallback
  return { route: "home", primary: "home", sub: "" };
}

export function navigate(route) {
  const target = route.startsWith("#") ? route : `#/${route.replace(/^\//, "")}`;
  if (window.location.hash !== target) {
    window.location.hash = target;
  } else {
    notify();
  }
}

function notify() {
  const routeInfo = parseRoute();
  for (const fn of listeners) {
    try {
      fn(routeInfo);
    } catch (err) {
      console.error("Router listener error:", err);
    }
  }
}

export function initRouter(callback) {
  if (callback) listeners.add(callback);
  window.addEventListener("hashchange", notify);
  // Initial dispatch
  notify();
}

export function subscribeRoute(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
