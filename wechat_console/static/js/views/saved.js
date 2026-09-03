/* Saved Messages View.
 *
 * Dual-pane browser for saved messages snapshots, tags, annotations,
 * and archived permanent media.
 */

import { state, setState } from "../state.js";
import { api } from "../api.js";
import { escapeHtml, escapeAttr, fmtWhen, fmtDateTime, fmtBytes } from "../format.js";
import { icon } from "../icons.js";
import { confirmAction } from "../components/confirm.js";
import { toast } from "../components/toast.js";

let mobilePane = "list"; // "list" | "detail"

/**
 * Render Saved Messages View.
 * @param {HTMLElement} container
 * @param {() => Promise<void>} reloadData
 */
export function renderSavedView(container, reloadData) {
  const allSaved = state.saved || [];
  const currentFilter = state.savedFilter || "all";
  const currentQuery = (state.savedQuery || "").trim().toLowerCase();

  // Filter items
  const filteredSaved = allSaved.filter((item) => {
    const snap = item.snapshot || {};
    const text = (snap.text || item.title || "").toLowerCase();
    const note = (item.note || "").toLowerCase();
    const tags = Array.isArray(item.tags) ? item.tags.join(" ").toLowerCase() : "";
    const type = snap.type || "";

    // Category match
    if (currentFilter === "image" && type !== "image") return false;
    if (
      currentFilter === "file" &&
      !(["file", "video", "audio"].includes(type) || snap.filename)
    ) {
      return false;
    }
    if (
      currentFilter === "link" &&
      !(text.includes("http://") || text.includes("https://"))
    ) {
      return false;
    }
    if (currentFilter === "annotated" && !item.note) return false;

    // Text search query
    if (currentQuery) {
      const match =
        (item.title || "").toLowerCase().includes(currentQuery) ||
        text.includes(currentQuery) ||
        note.includes(currentQuery) ||
        tags.includes(currentQuery) ||
        (snap.sender_name || "").toLowerCase().includes(currentQuery);
      if (!match) return false;
    }

    return true;
  });

  if (!state.selectedSavedId && filteredSaved.length > 0) {
    state.selectedSavedId = filteredSaved[0].saved_message_id;
  }

  const selectedItem =
    allSaved.find((item) => item.saved_message_id === state.selectedSavedId) ||
    filteredSaved[0] ||
    null;

  // Empty state if total saved is 0
  if (allSaved.length === 0) {
    container.innerHTML = `
      <div class="page-inner wide">
        <div class="page-head">
          <div>
            <div class="page-title">收藏</div>
            <p class="page-subtitle">重要消息的快照、注释和永久附件归档。</p>
          </div>
        </div>
        <div class="surface">
          <div class="empty">
            <div class="empty-icon">${icon("star")}</div>
            <div class="empty-title">还没有收藏</div>
            <p class="empty-text">在消息中点击「收藏」，重要内容会出现在这里。</p>
          </div>
        </div>
      </div>
    `;
    return;
  }

  // Chips HTML
  const chips = [
    { id: "all", label: "全部" },
    { id: "image", label: "图片" },
    { id: "file", label: "文件" },
    { id: "link", label: "链接" },
    { id: "annotated", label: "带注释" },
  ];

  const chipsHtml = chips
    .map(
      (c) =>
        `<button class="chip" data-filter="${c.id}" aria-pressed="${
          currentFilter === c.id ? "true" : "false"
        }">${escapeHtml(c.label)}</button>`
    )
    .join("");

  // Render list items
  let listHtml = "";
  if (filteredSaved.length === 0) {
    listHtml = `<div style="padding: 24px 16px; text-align: center; color: var(--text-secondary);">无匹配的收藏</div>`;
  } else {
    listHtml = filteredSaved
      .map((item) => {
        const isSelected = item.saved_message_id === state.selectedSavedId;
        const snap = item.snapshot || {};
        const type = snap.type || "text";
        const iconName =
          type === "image"
            ? "image"
            : ["file", "video", "audio"].includes(type) || snap.filename
            ? "file"
            : (snap.text || "").includes("http")
            ? "link"
            : "message";

        const title = item.title || snap.text || snap.filename || "未命名收藏";
        const when = fmtWhen(item.saved_at || item.created_at);
        const sender = snap.sender_name || snap.sender || snap.account_id || "";

        return `
          <button class="chat-item" data-saved-id="${escapeAttr(
            item.saved_message_id
          )}" aria-selected="${isSelected ? "true" : "false"}">
            <span class="avatar avatar-sm">${icon(iconName, { size: "sm" })}</span>
            <span class="chat-item-body">
              <span class="chat-item-name">${escapeHtml(title)}</span>
              <span class="chat-item-meta">${escapeHtml(sender ? `${sender} · ${when}` : when)}</span>
            </span>
          </button>
        `;
      })
      .join("");
  }

  // Render detail pane
  let detailHtml = "";
  if (!selectedItem) {
    detailHtml = `
      <div class="empty" style="padding: 48px var(--gutter);">
        <div class="empty-icon">${icon("star")}</div>
        <div class="empty-title">选择一条收藏</div>
        <p class="empty-text">在左侧列表选择收藏查看快照与归档。</p>
      </div>
    `;
  } else {
    const snap = selectedItem.snapshot || {};
    const tags = Array.isArray(selectedItem.tags) ? selectedItem.tags : [];
    const tagsStr = tags.join(", ");
    const mediaList = Array.isArray(selectedItem.media) ? selectedItem.media : [];

    const tagBadgesHtml = tags
      .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
      .join("");

    let mediaItemsHtml = "";
    if (mediaList.length > 0) {
      mediaItemsHtml = mediaList
        .map((m) => {
          const isArchived = m.status === "archived";
          const sizeLabel = m.bytes ? fmtBytes(m.bytes) : "--";
          const url = api.savedMediaUrl(m.saved_media_id);

          return `
            <div class="archive-item" style="display: flex; align-items: center; justify-content: space-between; padding: 12px var(--sp-3); border: 1px solid var(--border); border-radius: var(--r-sm); margin-bottom: 8px;">
              <div>
                <div class="item-title">${escapeHtml(m.filename || "媒体附件")}</div>
                <div class="caption">${isArchived ? `已永久归档 · ${sizeLabel}` : "待归档"}</div>
              </div>
              ${
                isArchived
                  ? `<a class="btn btn-secondary btn-sm" href="${escapeAttr(url)}" target="_blank" rel="noopener">打开</a>`
                  : `<button class="btn btn-ghost btn-sm" data-archive-retry="${escapeAttr(selectedItem.saved_message_id)}">重试归档</button>`
              }
            </div>
          `;
        })
        .join("");
    } else {
      mediaItemsHtml = `<p class="caption" style="color: var(--text-tertiary); margin: 0;">无附件或无需归档。</p>`;
    }

    detailHtml = `
      <div class="chat-toolbar" style="padding: 12px 16px;">
        <button class="btn btn-icon btn-sm split-back" id="savedMobileBackBtn" aria-label="返回列表">
          ${icon("chevronLeft", { size: "sm" })}
        </button>
        <div class="chat-toolbar-title">
          <div class="item-title">${escapeHtml(selectedItem.title || "收藏详情")}</div>
          <div class="caption">${escapeHtml(snap.account_id || "")} · 收藏于 ${fmtDateTime(selectedItem.saved_at || selectedItem.created_at)}</div>
        </div>
        <button class="btn btn-icon btn-sm" id="savedDeleteBtn" title="删除此收藏" aria-label="删除此收藏" style="color: var(--danger-text);">
          ${icon("trash", { size: "sm" })}
        </button>
      </div>

      <div class="scroll-y" style="padding: 20px; display: flex; flex-direction: column; gap: 16px;">
        <!-- Snapshot Box -->
        <div class="snapshot">
          <strong>${escapeHtml(snap.sender_name || snap.sender || "发送者")}</strong>
          <span style="white-space: pre-wrap;">${escapeHtml(snap.text || snap.filename || "[媒体内容]")}</span>
          <span class="caption mono">${escapeHtml(snap.type || "text")} · ${fmtDateTime(snap.timestamp || snap.created_at)} · ${escapeHtml(snap.message_id || "")}</span>
          ${tagBadgesHtml ? `<div class="tag-row" style="margin-top: 8px;">${tagBadgesHtml}</div>` : ""}
        </div>

        <!-- Editable Annotation Form -->
        <div class="field">
          <label class="label" for="savedTitleInput">标题</label>
          <input class="input" id="savedTitleInput" value="${escapeAttr(selectedItem.title || "")}" />
        </div>
        <div class="field">
          <label class="label" for="savedTagsInput">标签</label>
          <input class="input" id="savedTagsInput" value="${escapeAttr(tagsStr)}" placeholder="例如：研究, 图片, 会议" />
        </div>
        <div class="field">
          <label class="label" for="savedNoteInput">注释</label>
          <textarea class="textarea" id="savedNoteInput" rows="3" placeholder="添加备注或说明…">${escapeHtml(selectedItem.note || "")}</textarea>
        </div>

        <div style="display: flex; gap: 8px;">
          <button class="btn btn-primary btn-sm" id="savedSaveBtn">保存更改</button>
          <button class="btn btn-ghost btn-sm" id="savedArchiveAllBtn">重试附件归档</button>
        </div>

        <!-- Archived Media Section -->
        <div class="section" style="margin-top: 12px;">
          <h3 class="section-title" style="font-size: 15px; margin-bottom: 8px;">附件归档</h3>
          ${mediaItemsHtml}
        </div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="page-inner wide">
      <div class="page-head">
        <div>
          <div class="page-title">收藏</div>
          <p class="page-subtitle">重要消息的快照、注释和永久附件归档。</p>
        </div>
      </div>

      <div class="chips" id="savedChipsRoot">${chipsHtml}</div>

      <div class="surface surface-flush">
        <div class="split" data-pane="${mobilePane}">
          <!-- Left Column -->
          <div class="split-side">
            <div style="padding: 12px 16px; border-bottom: 1px solid var(--border)">
              <div class="search">
                ${icon("search", { size: "sm" })}
                <input class="input" id="savedSearchInput" placeholder="搜索收藏" value="${escapeAttr(
                  state.savedQuery || ""
                )}" />
              </div>
            </div>
            <div class="chat-list scroll-y" id="savedListRoot">${listHtml}</div>
          </div>

          <!-- Right Column -->
          <div class="split-main" id="savedDetailRoot">${detailHtml}</div>
        </div>
      </div>
    </div>
  `;

  // Wire Filter Chips
  container.querySelectorAll(".chip[data-filter]").forEach((chip) => {
    chip.onclick = () => {
      state.savedFilter = chip.dataset.filter;
      renderSavedView(container, reloadData);
    };
  });

  // Wire Search
  const searchInput = container.querySelector("#savedSearchInput");
  if (searchInput) {
    searchInput.oninput = () => {
      state.savedQuery = searchInput.value;
      renderSavedView(container, reloadData);
    };
  }

  // Wire list item clicks
  container.querySelectorAll(".chat-item[data-saved-id]").forEach((item) => {
    item.onclick = () => {
      state.selectedSavedId = item.dataset.savedId;
      mobilePane = "detail";
      renderSavedView(container, reloadData);
    };
  });

  // Wire mobile back
  const backBtn = container.querySelector("#savedMobileBackBtn");
  if (backBtn) {
    backBtn.onclick = () => {
      mobilePane = "list";
      renderSavedView(container, reloadData);
    };
  }

  // Wire Save button
  const saveBtn = container.querySelector("#savedSaveBtn");
  if (saveBtn && selectedItem) {
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      const title = container.querySelector("#savedTitleInput").value.trim();
      const tagsRaw = container.querySelector("#savedTagsInput").value;
      const tags = tagsRaw
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean);
      const note = container.querySelector("#savedNoteInput").value.trim();

      try {
        await api.updateSaved(selectedItem.saved_message_id, {
          title: title || selectedItem.title,
          tags,
          note,
        });
        toast({ title: "已保存收藏修改", tone: "good" });
        await reloadData();
      } catch (err) {
        toast({ title: "修改失败", text: err.message, tone: "bad" });
        saveBtn.disabled = false;
      }
    };
  }

  // Wire Archive retry
  const archiveBtn = container.querySelector("#savedArchiveAllBtn");
  if (archiveBtn && selectedItem) {
    archiveBtn.onclick = async () => {
      archiveBtn.disabled = true;
      try {
        await api.archiveSaved(selectedItem.saved_message_id);
        toast({ title: "已触发归档", tone: "good" });
        await reloadData();
      } catch (err) {
        toast({ title: "归档失败", text: err.message, tone: "bad" });
        archiveBtn.disabled = false;
      }
    };
  }

  // Wire Single Item Archive retry buttons
  container.querySelectorAll("button[data-archive-retry]").forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const savedId = btn.dataset.archiveRetry;
      if (!savedId) return;

      btn.disabled = true;
      btn.textContent = "正在归档…";
      try {
        await api.archiveSaved(savedId);
        toast({ title: "已重新触发附件归档", tone: "good" });
        await reloadData();
      } catch (err) {
        toast({ title: "归档重试失败", text: err.message, tone: "bad" });
        btn.disabled = false;
        btn.textContent = "重试归档";
      }
    };
  });

  // Wire Delete button
  const deleteBtn = container.querySelector("#savedDeleteBtn");
  if (deleteBtn && selectedItem) {
    deleteBtn.onclick = async () => {
      const confirmed = await confirmAction({
        title: "移除此收藏？",
        text: "移除后快照与归档文件将被删除。",
        confirmLabel: "确认移除",
        tone: "danger",
      });
      if (!confirmed) return;
      try {
        await api.deleteSaved(selectedItem.saved_message_id);
        toast({ title: "已移除收藏", tone: "good" });
        state.selectedSavedId = "";
        mobilePane = "list";
        await reloadData();
      } catch (err) {
        toast({ title: "移除失败", text: err.message, tone: "bad" });
      }
    };
  }
}
