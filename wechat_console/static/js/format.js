/* Formatting helpers shared by every view. */

export function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
  );
}

export function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

export function fmtNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Full timestamp — used in advanced/diagnostic surfaces. */
export function fmtDateTime(value) {
  const date = parseDate(value);
  return date ? date.toLocaleString("zh-CN", { hour12: false }) : "--";
}

/** Short, human timestamp — "09:41" today, "昨天 09:41", "8月30日" earlier. */
export function fmtWhen(value) {
  const date = parseDate(value);
  if (!date) return "";
  const now = new Date();
  const time = date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const sameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(date, now)) return time;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(date, yesterday)) return `昨天 ${time}`;
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }
  return date.toLocaleDateString("zh-CN");
}

/** "今天 09:21 登录" style relative sentence used on account rows. */
export function fmtLastActivity(value, suffix = "") {
  const when = fmtWhen(value);
  if (!when) return "";
  const date = parseDate(value);
  const now = new Date();
  const isToday =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  const label = isToday ? `今天 ${when}` : when;
  return suffix ? `${label} ${suffix}` : label;
}

export function fmtBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const size = bytes / 1024 ** index;
  return `${index === 0 ? size : size.toFixed(size < 10 ? 1 : 0)} ${units[index]}`;
}

/** First display glyph for an avatar; CJK-friendly. */
export function initial(name, fallback = "微") {
  const text = String(name || "").trim();
  if (!text) return fallback;
  return [...text][0];
}
