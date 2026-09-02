/* Theme boot — must run before first paint, so it is loaded as a classic,
 * non-deferred <script> in <head> and kept deliberately tiny.
 *
 * The stored value is a *preference* ("system" | "light" | "dark"); the
 * resolved value is always materialised as data-theme="light|dark" on <html>.
 * That way CSS only ever needs one dark palette, and "follow the system" costs
 * no duplicated tokens.
 */
(function () {
  var KEY = "wechat-hub.theme";
  var root = document.documentElement;

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return value === "light" || value === "dark" || value === "system" ? value : "system";
    } catch (_) {
      // Private mode / disabled storage: fall back to following the system.
      return "system";
    }
  }

  var query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function resolve(preference) {
    if (preference === "light" || preference === "dark") return preference;
    return query && query.matches ? "dark" : "light";
  }

  function apply(preference) {
    root.dataset.theme = resolve(preference);
    root.dataset.themePreference = preference;
  }

  apply(stored());

  // Live OS switch: only meaningful while the preference is "system".
  if (query) {
    var onChange = function () {
      if (root.dataset.themePreference === "system") apply("system");
    };
    if (query.addEventListener) query.addEventListener("change", onChange);
    else if (query.addListener) query.addListener(onChange);
  }

  // Minimal API for the settings view; kept on window because this file is a
  // classic script that runs before any module.
  window.__wechatHubTheme = {
    key: KEY,
    get preference() {
      return root.dataset.themePreference || "system";
    },
    get resolved() {
      return root.dataset.theme || "light";
    },
    set: function (preference) {
      var next = preference === "light" || preference === "dark" ? preference : "system";
      try {
        localStorage.setItem(KEY, next);
      } catch (_) {
        /* preference simply does not persist */
      }
      apply(next);
      window.dispatchEvent(
        new CustomEvent("wechat-hub:themechange", {
          detail: { preference: next, resolved: root.dataset.theme },
        }),
      );
    },
  };
})();
