/* Shared icon sprite.
 *
 * One stroke-only 24px grid, 1.7px stroke, round caps — the single icon style
 * used by both the design concepts and the running Console.  The sprite is
 * injected inline so `<use href="#i-...">` works without an extra request and
 * without cross-document `use` quirks.
 */

const PATHS = {
  home: '<path d="M4 10.4 12 4l8 6.4V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1z"/>',
  wechat:
    '<path d="M9.2 4.5C5.8 4.5 3 6.8 3 9.6c0 1.6.9 3 2.3 4L4.6 16l2.5-1.2c.6.2 1.3.3 2.1.3h.5"/><path d="M15.2 9.3c-3 0-5.5 2-5.5 4.5s2.5 4.5 5.5 4.5c.7 0 1.3-.1 1.9-.3l2.3 1.1-.7-2.1c1.2-.8 2-2 2-3.2 0-2.5-2.5-4.5-5.5-4.5Z"/>',
  message:
    '<path d="M20 12.5c0 3.6-3.4 6.5-7.6 6.5-.9 0-1.8-.1-2.6-.4L5 20l1.2-3.1C4.8 15.7 4 14.2 4 12.5 4 8.9 7.6 6 12 6s8 2.9 8 6.5Z"/>',
  star: '<path d="m12 4.8 2.3 4.7 5.2.8-3.8 3.6.9 5.1-4.6-2.4-4.6 2.4.9-5.1-3.8-3.6 5.2-.8z"/>',
  automation:
    '<rect x="4" y="8" width="16" height="11" rx="2.5"/><path d="M12 4.5V8M9 13h.01M15 13h.01M9.5 16.2h5"/>',
  settings:
    '<circle cx="12" cy="12" r="2.8"/><path d="M19.4 14.2a1.4 1.4 0 0 0 .3 1.5l.1.1a1.7 1.7 0 1 1-2.4 2.4l-.1-.1a1.4 1.4 0 0 0-2.4 1v.2a1.7 1.7 0 1 1-3.4 0v-.1a1.4 1.4 0 0 0-2.4-1l-.1.1a1.7 1.7 0 1 1-2.4-2.4l.1-.1a1.4 1.4 0 0 0-1-2.4H5.6a1.7 1.7 0 1 1 0-3.4h.1a1.4 1.4 0 0 0 1-2.4l-.1-.1a1.7 1.7 0 1 1 2.4-2.4l.1.1a1.4 1.4 0 0 0 2.4-1V5.6a1.7 1.7 0 1 1 3.4 0v.1a1.4 1.4 0 0 0 2.4 1l.1-.1a1.7 1.7 0 1 1 2.4 2.4l-.1.1a1.4 1.4 0 0 0 1 2.4h.2a1.7 1.7 0 1 1 0 3.4h-.1a1.4 1.4 0 0 0-1.3.8Z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh:
    '<path d="M20 11a8 8 0 1 0-.7 4.3"/><path d="M20 5.5V11h-5.5"/>',
  more: '<circle cx="6" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18" cy="12" r="1.3"/>',
  chevronRight: '<path d="m9.5 5.5 6.5 6.5-6.5 6.5"/>',
  chevronLeft: '<path d="M14.5 5.5 8 12l6.5 6.5"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="m20 20-3.6-3.6"/>',
  image:
    '<rect x="4" y="5.5" width="16" height="13" rx="2.5"/><circle cx="9" cy="10" r="1.4"/><path d="m5 16.5 4.2-3.7 3.3 2.8 2.4-2 4.1 3.4"/>',
  file: '<path d="M13.5 4.5H7.5a1.5 1.5 0 0 0-1.5 1.5v12a1.5 1.5 0 0 0 1.5 1.5h9a1.5 1.5 0 0 0 1.5-1.5V9z"/><path d="M13.5 4.5V9H18"/>',
  check: '<path d="m5.5 12.5 4.2 4.2L18.5 8"/>',
  alertTriangle:
    '<path d="M12 5.5 3.8 19.2h16.4z"/><path d="M12 10v4M12 16.6h.01"/>',
  alertCircle: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4.5M12 15.8h.01"/>',
  info: '<circle cx="12" cy="12" r="8"/><path d="M12 11v5M12 8.2h.01"/>',
  close: '<path d="M6.5 6.5 17.5 17.5M17.5 6.5 6.5 17.5"/>',
  external:
    '<path d="M14 5h5v5"/><path d="M19 5 11 13"/><path d="M18.5 13.8V18a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V7A1.5 1.5 0 0 1 6 5.5h4.2"/>',
  menu: '<path d="M4.5 7.5h15M4.5 12h15M4.5 16.5h15"/>',
  power: '<path d="M12 4.5V12"/><path d="M17.3 7.2a7.5 7.5 0 1 1-10.6 0"/>',
  play: '<path d="M8.5 5.6 18 12l-9.5 6.4z"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
  trash:
    '<path d="M5.5 7h13"/><path d="M9.5 7V5.5A1 1 0 0 1 10.5 4.5h3a1 1 0 0 1 1 1V7"/><path d="M7 7v11.5a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V7"/><path d="M10.5 10.5v6M13.5 10.5v6"/>',
  qr: '<rect x="4.5" y="4.5" width="6" height="6" rx="1.2"/><rect x="13.5" y="4.5" width="6" height="6" rx="1.2"/><rect x="4.5" y="13.5" width="6" height="6" rx="1.2"/><path d="M13.5 13.5h2.5v2.5h-2.5zM19.5 13.5v2.5M17 19.5h2.5M13.5 19.5h1"/>',
  inbox:
    '<path d="M4.5 13.5 6.8 6a1.5 1.5 0 0 1 1.4-1h7.6a1.5 1.5 0 0 1 1.4 1l2.3 7.5"/><path d="M4.5 13.5H9a3 3 0 0 0 6 0h4.5V18a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18z"/>',
  bell: '<path d="M17.5 11.5a5.5 5.5 0 0 0-11 0c0 4.2-1.5 5.4-1.5 5.4h14s-1.5-1.2-1.5-5.4Z"/><path d="M13.7 19.5a2 2 0 0 1-3.4 0"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 1.8"/>',
  sparkle:
    '<path d="m12 4.5 1.7 4.3 4.3 1.7-4.3 1.7L12 16.5l-1.7-4.3L6 10.5l4.3-1.7z"/><path d="M18 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  telegram: '<path d="M20 5 3.8 11.2l4.6 1.6L18 7l-7.6 7.2.3 4.4 2.6-3.2 4 3z"/>',
  database:
    '<ellipse cx="12" cy="6.5" rx="7" ry="2.8"/><path d="M5 6.5v11c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-11"/><path d="M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8"/>',
  shield: '<path d="M12 4.2 5.5 6.6v5c0 3.7 2.6 7 6.5 8.2 3.9-1.2 6.5-4.5 6.5-8.2v-5z"/>',
  link: '<path d="M10.5 13.5a3.5 3.5 0 0 0 5 0l2.5-2.5a3.5 3.5 0 1 0-5-5l-1.2 1.2"/><path d="M13.5 10.5a3.5 3.5 0 0 0-5 0L6 13a3.5 3.5 0 1 0 5 5l1.2-1.2"/>',
  arrowLeft: '<path d="M19 12H5.5"/><path d="M11 5.5 4.5 12l6.5 6.5"/>',
};

export const ICON_SPRITE = `<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">${Object.entries(
  PATHS,
)
  .map(
    ([name, body]) =>
      `<symbol id="i-${name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${body}</symbol>`,
  )
  .join("")}</svg>`;

export function icon(name, { size = "" } = {}) {
  const cls = size === "sm" ? "icon icon-sm" : "icon";
  return `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

export function mountIconSprite(doc = document) {
  if (doc.getElementById("icon-sprite-root")) return;
  const holder = doc.createElement("div");
  holder.id = "icon-sprite-root";
  holder.innerHTML = ICON_SPRITE;
  doc.body.prepend(holder);
}
