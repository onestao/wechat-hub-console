/* Status indicator component.
 *
 * Triple encoding: shape glyph + copy + semantic tone color.
 */

import { escapeHtml } from "../format.js";

/**
 * Render .status element markup.
 * @param {"good"|"warn"|"bad"|"busy"|"idle"} tone
 * @param {string} text
 * @returns {string}
 */
export function statusMarkup(tone, text) {
  const safeTone = escapeHtml(tone || "idle");
  const safeText = escapeHtml(text || "");
  return `<span class="status" data-tone="${safeTone}"><span class="status-glyph"></span>${safeText}</span>`;
}
