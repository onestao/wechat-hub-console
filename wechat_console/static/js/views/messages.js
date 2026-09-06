/* Messages View (v2).
 *
 * Scoped message loading, cursor pagination, visual scroll anchoring,
 * strict auto-scroll rules, capability-aware composer, Send Gate identity
 * protection, and saved messages integration.
 */

import { state, setState } from "../state.js";
import { api } from "../api.js";
import { capabilitiesOf } from "../capabilities.js";
import { accountSwitchLabel } from "../account-view-model.js";
import { escapeHtml, escapeAttr, fmtWhen, fmtDateTime, initial } from "../format.js";
import { icon } from "../icons.js";
import { confirmAction } from "../components/confirm.js";
import { toast } from "../components/toast.js";
import { openDialog, closeDialog } from "../components/dialog.js";

let saveDialogEl = null;
let activeSendWatcher = null;
let mobilePane = "list"; // "list" | "detail"

// Scoped message state & pagination
let activeChatKey = null;
let chatMessages = [];
let messagesCursor = "";
let messagesHasMore = false;
let isLoadingOlder = false;
let isFetchingScoped = false;
let lastContainer = null;
let lastReloadData = null;

export function resolveChatName(chat) {
  if (!chat) return "未选择会话";
  const name = (chat.display_name || chat.alias || chat.name || "").trim();
  if (name) return name;
  return chat.chat_id || "未命名会话";
}

export function isGroupChat(chat) {
  if (!chat) return false;
  return (
    chat.type === "group" ||
    Boolean(chat.is_group) ||
    Boolean(chat.chat_id && chat.chat_id.endsWith("@chatroom"))
  );
}

export function isOutgoingMessage(m) {
  if (!m) return false;
  return (
    m.direction === "outgoing" ||
    Boolean(m.author?.is_self) ||
    Boolean(m.is_self) ||
    Boolean(m.is_sender) ||
    Boolean(m.is_outgoing) ||
    m.from_user === "me"
  );
}

export function resolveAuthorName(m, chat) {
  if (isOutgoingMessage(m)) {
    return "我";
  }
  const author = m.author || {};
  const name = (
    author.display_name ||
    author.alias ||
    author.member_id ||
    m.sender_name ||
    m.sender ||
    ""
  ).trim();
  if (name) {
    return name;
  }
  const isGroup = isGroupChat(chat);
  if (isGroup) {
    // 群聊中禁止 fallback 为“对方”
    return (author.member_id || m.sender_id || m.sender || m.from_user || "群成员").trim();
  }
  return (
    author.member_id ||
    m.sender_id ||
    m.sender ||
    m.from_user ||
    (chat ? resolveChatName(chat) : "") ||
    ""
  ).trim();
}

export function sortMessagesAsc(list) {
  return list.slice().sort((a, b) => {
    const ta = a.created_at || a.timestamp || a.occurred_at || "";
    const tb = b.created_at || b.timestamp || b.occurred_at || "";
    if (ta < tb) return -1;
    if (ta > tb) return 1;
    const ida = a.message_id || "";
    const idb = b.message_id || "";
    return String(ida).localeCompare(String(idb));
  });
}


function renderAvatar(authorName, avatarUrl, isSelf) {
  if (avatarUrl) {
    return `<img class="avatar avatar-sm bubble-avatar-img" src="${escapeAttr(
      avatarUrl
    )}" alt="${escapeAttr(authorName)}" style="width: 28px; height: 28px; border-radius: var(--r-sm); object-fit: cover;" />`;
  }
  const glyph = initial(authorName, isSelf ? "我" : "友");
  return `<span class="avatar avatar-sm bubble-avatar-glyph" style="width: 28px; height: 28px; font-size: 11px;">${escapeHtml(
    glyph
  )}</span>`;
}

function getActiveAccount() {
  const accounts = state.runtimeAccounts.length > 0 ? state.runtimeAccounts : state.accounts;
  return accounts.find((a) => a.account_id === state.activeAccountId) || accounts[0] || null;
}

async function fetchScopedMessages(
  accountId,
  chatId,
  { isNewChat = false, isPrepend = false, before = "", oldScrollTop = 0, oldScrollHeight = 0 } = {}
) {
  if (!accountId || !chatId) {
    chatMessages = [];
    messagesCursor = "";
    messagesHasMore = false;
    if (lastContainer) renderMessagesView(lastContainer, lastReloadData);
    return;
  }

  isFetchingScoped = true;
  const activeAccount = getActiveAccount();

  try {
    const params = {
      account_id: accountId,
      chat_id: chatId,
      limit: 100,
    };
    if (activeAccount?.instance_uuid) {
      params.instance_uuid = activeAccount.instance_uuid;
    }
    if (activeAccount?.wechat_identity_uuid) {
      params.wechat_identity_uuid = activeAccount.wechat_identity_uuid;
    }
    if (before) {
      params.before = before;
    }

    const res = await api.messages(params);
    const fetched = res.messages || [];
    messagesCursor = res.next_cursor || "";
    messagesHasMore = Boolean(res.has_more);

    const existingIds = new Set(chatMessages.map((m) => m.message_id));
    if (isPrepend) {
      const uniqueOlder = fetched.filter((m) => !existingIds.has(m.message_id));
      chatMessages = sortMessagesAsc([...uniqueOlder, ...chatMessages]);
    } else {
      chatMessages = sortMessagesAsc(fetched);
    }

    if (lastContainer) {
      renderMessagesView(lastContainer, lastReloadData, {
        isNewChat,
        isPrepend,
        oldScrollTop,
        oldScrollHeight,
      });
    }
  } catch (err) {
    console.error("Scoped message fetch failed:", err);
  } finally {
    isFetchingScoped = false;
    isLoadingOlder = false;
  }
}

function renderChatItemsHtml(filteredChats, selectedChatId) {
  if (filteredChats.length === 0) {
    return `<div style="padding: 24px 16px; text-align: center; color: var(--text-secondary);">暂无会话</div>`;
  }
  return filteredChats
    .map((c) => {
      const isSelected = c.chat_id === selectedChatId;
      const displayName = resolveChatName(c);
      const isGroup = isGroupChat(c);
      const initialGlyph = initial(displayName, isGroup ? "群" : "会");
      const snippet = c.last_message_snippet || c.last_message || "";
      const timeStr = c.last_message_time || c.updated_at ? fmtWhen(c.last_message_time || c.updated_at) : "";

      return `
        <button class="chat-item" data-chat-id="${escapeAttr(
          c.chat_id
        )}" aria-selected="${isSelected ? "true" : "false"}">
          <span class="avatar avatar-sm">${escapeHtml(initialGlyph)}</span>
          <span class="chat-item-body">
            <span class="chat-item-header-line" style="display: flex; align-items: center; justify-content: space-between; gap: 6px;">
              <span class="chat-item-name" style="flex: 1; min-width: 0;">${escapeHtml(displayName)}</span>
              ${isGroup ? `<span class="badge" style="font-size: 10px; padding: 1px 5px; flex-shrink: 0;">群聊${c.member_count ? `(${c.member_count})` : ""}</span>` : ""}
              ${timeStr ? `<span class="chat-item-time caption" style="font-size: 11px; color: var(--text-tertiary); flex-shrink: 0;">${escapeHtml(timeStr)}</span>` : ""}
            </span>
            ${
              snippet
                ? `<span class="chat-item-meta">${escapeHtml(snippet)}</span>`
                : ""
            }
          </span>
        </button>
      `;
    })
    .join("");
}

function getFilteredChats() {
  const chatQuery = (state.messageQuery || "").trim().toLowerCase();
  const allChats = state.chats || [];
  return chatQuery
    ? allChats.filter((c) => {
        const name = resolveChatName(c).toLowerCase();
        const cid = (c.chat_id || "").toLowerCase();
        return name.includes(chatQuery) || cid.includes(chatQuery);
      })
    : allChats;
}

function renderChatListOnly(container, reloadData) {
  const chatListRoot = container.querySelector("#messagesChatListRoot");
  if (!chatListRoot) return;
  const filteredChats = getFilteredChats();
  chatListRoot.innerHTML = renderChatItemsHtml(filteredChats, state.selectedChatId);

  chatListRoot.querySelectorAll(".chat-item[data-chat-id]").forEach((item) => {
    item.onclick = () => {
      const newChatId = item.dataset.chatId;
      mobilePane = "detail";
      if (state.selectedChatId !== newChatId) {
        state.selectedChatId = newChatId;
        renderMessagesView(container, reloadData);
      } else {
        renderMessagesView(container, reloadData);
      }
    };
  });
}


function readFileAsBase64(file, { imageOnly = false } = {}) {
  return new Promise((resolve, reject) => {
    const isImg = (file.type && file.type.startsWith("image/")) ||
      /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(file.name || "");
    if (imageOnly && !isImg) {
      reject(new Error("请选择图片文件（如 PNG、JPEG、GIF 等）"));
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      reject(new Error("文件不能超过 20 MB"));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result || "";
      const commaIdx = result.indexOf(",");
      if (commaIdx === -1) {
        reject(new Error("读取文件数据失败"));
        return;
      }
      resolve(result.slice(commaIdx + 1));
    };
    reader.onerror = () => {
      reject(new Error("读取文件失败，请重试"));
    };
    reader.readAsDataURL(file);
  });
}

/**
 * Render Messages View.
 * @param {HTMLElement} container
 * @param {() => Promise<void>} reloadData
 * @param {object} [options]
 */
export function renderMessagesView(container, reloadData, options = {}) {
  lastContainer = container;
  lastReloadData = reloadData;

  const {
    isNewChat = false,
    isPrepend = false,
    oldScrollTop = 0,
    oldScrollHeight = 0,
    justSent = false,
  } = options;

  const accounts = state.runtimeAccounts.length > 0 ? state.runtimeAccounts : state.accounts;
  if (!state.activeAccountId && accounts.length > 0) {
    state.activeAccountId = accounts[0].account_id;
  }

  const activeAccount = accounts.find((a) => a.account_id === state.activeAccountId) || accounts[0] || null;
  const coreAccount = (state.accounts || []).find((a) => a.account_id === state.activeAccountId) || null;

  const caps = capabilitiesOf(activeAccount || coreAccount, {
    coreCapabilities: coreAccount?.runtime?.sender_capabilities,
  });

  // Filter chats by query
  const allChats = state.chats || [];
  const filteredChats = getFilteredChats();

  if (!state.selectedChatId && filteredChats.length > 0) {
    state.selectedChatId = filteredChats[0].chat_id;
  }

    const selectedChat = allChats.find((c) => c.chat_id === state.selectedChatId) || filteredChats[0] || null;

  // Check if activeChatKey changed -> trigger scoped loading
  const currentChatKey = `${state.activeAccountId || ""}:${state.selectedChatId || ""}`;
  if (currentChatKey && currentChatKey !== activeChatKey) {
    activeChatKey = currentChatKey;
    chatMessages = [];
    messagesCursor = "";
    messagesHasMore = false;
    fetchScopedMessages(state.activeAccountId, state.selectedChatId, { isNewChat: true });
  }

  // Account Switcher options — C8: nickname — display_name, never bare account_id
  const accountSwitcherOptions = accounts
    .map(
      (a) =>
        `<option value="${escapeAttr(a.account_id)}" ${
          a.account_id === state.activeAccountId ? "selected " : ""
        }>${escapeHtml(accountSwitchLabel(a))}</option>`
    )
    .join("");

  // Render Chat list items
  const chatListHtml = renderChatItemsHtml(filteredChats, state.selectedChatId);

  // Filter messages by selected type
  let messagesToDisplay = chatMessages;
  if (state.selectedMessageType) {
    messagesToDisplay = chatMessages.filter((m) => m.type === state.selectedMessageType);
  }

  // Render Message Thread
  const selectedChatName = resolveChatName(selectedChat);
  const selectedIsGroup = isGroupChat(selectedChat);

  let loadOlderHtml = "";
  if (messagesHasMore) {
    loadOlderHtml = `
      <div class="load-older-wrap" style="text-align: center; padding: 4px 0 8px;">
        <button class="btn btn-secondary btn-sm" id="messagesLoadOlderBtn" ${isLoadingOlder ? "disabled" : ""}>
          ${isLoadingOlder ? "正在加载更早消息…" : "加载更早消息"}
        </button>
      </div>
    `;
  }

  let threadHtml = "";
  if (!selectedChat) {
    threadHtml = `
      <div class="empty" style="padding: 48px var(--gutter);">
        <div class="empty-icon">${icon("message")}</div>
        <div class="empty-title">选择一个会话</div>
        <p class="empty-text">在左侧列表选择会话查看同步的消息记录。</p>
      </div>
    `;
  } else if (isFetchingScoped && chatMessages.length === 0) {
    threadHtml = `
      <div class="empty" style="padding: 48px var(--gutter);">
        <div class="empty-icon">${icon("message")}</div>
        <div class="empty-title">正在加载消息…</div>
        <p class="empty-text">正在获取已同步的消息记录。</p>
      </div>
    `;
  } else if (messagesToDisplay.length === 0) {
    threadHtml = `
      <div class="empty" style="padding: 48px var(--gutter);">
        <div class="empty-icon">${icon("message")}</div>
        <div class="empty-title">暂无消息</div>
        <p class="empty-text">${
          state.selectedMessageType ? "该分类下暂无消息记录。" : "该会话尚未同步到消息记录。"
        }</p>
      </div>
    `;
  } else {
    const bubblesHtml = messagesToDisplay
      .map((m) => {
        const isOutgoing = isOutgoingMessage(m);
        const authorName = resolveAuthorName(m, selectedChat);
        const when = fmtWhen(m.created_at || m.timestamp || m.occurred_at);
        const authorText = `${authorName} · ${when}`;
        const authorObj = m.author || {};
        const avatarUrl = authorObj.avatar_url || m.avatar_url || "";

        let attachmentHtml = "";
        if (m.media_id || m.type === "image" || m.type === "file" || m.type === "video") {
          const mediaUrl = m.media_id
            ? api.mediaUrl(m.media_id, m.account_id || state.activeAccountId)
            : "";
          if (m.type === "image" && mediaUrl) {
            attachmentHtml = `
              <div class="bubble-attachment" style="padding: 4px 0;">
                <img src="${escapeAttr(mediaUrl)}" alt="图片附件" style="max-width: 100%; max-height: 240px; border-radius: var(--r-sm); object-fit: contain;" />
              </div>
            `;
          } else if (m.filename || m.type === "file") {
            attachmentHtml = `
              <div class="bubble-attachment">
                ${icon("file", { size: "sm" })}
                <span>${escapeHtml(m.filename || "附件文件")}</span>
                ${mediaUrl ? `<a href="${escapeAttr(mediaUrl)}" target="_blank" rel="noopener">下载</a>` : ""}
              </div>
            `;
          }
        }

        const isRevoked = Boolean(m.removed || m.revoked || m.is_revoked);
        const textHtml = isRevoked
          ? `<em style="color: var(--text-tertiary);">[消息已撤回/移除]</em>`
          : escapeHtml(m.text || "");

        return `
          <div class="bubble-line ${isOutgoing ? "outgoing" : ""} ${isRevoked ? "removed" : ""}" data-msg-id="${escapeAttr(
            m.message_id || ""
          )}">
            <div class="bubble-author-row" style="display: flex; align-items: center; gap: 6px; margin-bottom: 2px; ${isOutgoing ? "justify-content: flex-end;" : ""}">
              ${!isOutgoing ? renderAvatar(authorName, avatarUrl, false) : ""}
              <span class="bubble-author">${escapeHtml(authorText)}</span>
              ${isOutgoing ? renderAvatar(authorName, avatarUrl, true) : ""}
            </div>
            <div class="bubble">
              ${textHtml ? `<div class="bubble-text">${textHtml}</div>` : ""}
              ${attachmentHtml}
              <button class="btn btn-icon btn-sm bubble-save-btn bubble-actions" data-save-msg="${escapeAttr(
                m.message_id || ""
              )}" aria-label="收藏此消息" title="收藏此消息">
                ${icon("star", { size: "sm" })}
              </button>
            </div>
            ${
              isOutgoing && !isRevoked
                ? `<div class="bubble-foot"><span>已发送</span></div>`
                : ""
            }
          </div>
        `;
      })
      .join("\n");

    threadHtml = `${loadOlderHtml}\n${bubblesHtml}`;
  }


  // Send Status Banner
  let sendStatusBannerHtml = "";
  if (state.sendResult) {
    const sr = state.sendResult;
    if (sr.status === "sending" || sr.status === "accepted" || sr.status === "queued") {
      sendStatusBannerHtml = `
        <div class="send-result" data-state="sending">
          <span class="status" data-tone="busy"><span class="status-glyph"></span></span>
          <div class="send-result-body">
            <div class="send-result-title">${sr.status === "sending" ? "发送中…" : "正在排队发送…"}</div>
          </div>
        </div>
      `;
    } else if (sr.status === "submitted") {
      sendStatusBannerHtml = `
        <div class="send-result" data-state="submitted">
          <span class="status" data-tone="warn"><span class="status-glyph"></span></span>
          <div class="send-result-body">
            <div class="send-result-title">已提交，等待微信确认</div>
            <div class="send-result-text">微信已接收提交，正在确认送达结果。</div>
          </div>
        </div>
      `;
    } else if (sr.status === "sent") {
      sendStatusBannerHtml = `
        <div class="send-result" data-state="sent">
          <div style="color: var(--brand-text); display: inline-flex;">${icon("check")}</div>
          <div class="send-result-body">
            <div class="send-result-title">已确认发送</div>
          </div>
        </div>
      `;
    } else if (sr.status === "failed") {
      let retryBtnHtml = "";
      if (sr.kind === "image") {
        retryBtnHtml = `<button class="btn btn-secondary btn-sm" id="msgRetryImageBtn">重新选择图片</button>`;
      } else if (sr.kind === "file") {
        retryBtnHtml = `<button class="btn btn-secondary btn-sm" id="msgRetryFileBtn">重新选择文件</button>`;
      } else {
        retryBtnHtml = `<button class="btn btn-secondary btn-sm" id="msgRetrySendBtn">重新发送</button>`;
      }

      const failTitle = sr.kind === "image" ? "图片发送失败" : sr.kind === "file" ? "文件发送失败" : "发送失败";
      const failText = sr.error || (sr.kind === "image" ? "微信没有接收这张图片，可以重新选择发送。" : sr.kind === "file" ? "微信没有接收该文件，可以重新选择发送。" : "微信没有接收这条消息，可以重新发送。");

      sendStatusBannerHtml = `
        <div class="send-result" data-state="failed">
          <div style="color: var(--danger); display: inline-flex;">${icon("alertCircle")}</div>
          <div class="send-result-body">
            <div class="send-result-title">${failTitle}</div>
            <div class="send-result-text">${escapeHtml(failText)}</div>
            <div class="send-result-actions">
              ${retryBtnHtml}
            </div>
          </div>
        </div>
      `;
    } else if (sr.status === "uncertain") {
      let uncertainActionsHtml = "";
      if (sr.kind === "image") {
        uncertainActionsHtml = `
          <button class="btn btn-secondary btn-sm" id="msgDismissUncertainBtn">查看消息</button>
          <button class="btn btn-ghost btn-sm" id="msgForceRetryImageBtn">重新选择图片</button>
        `;
      } else if (sr.kind === "file") {
        uncertainActionsHtml = `
          <button class="btn btn-secondary btn-sm" id="msgDismissUncertainBtn">查看消息</button>
          <button class="btn btn-ghost btn-sm" id="msgForceRetryFileBtn">重新选择文件</button>
        `;
      } else {
        uncertainActionsHtml = `
          <button class="btn btn-secondary btn-sm" id="msgDismissUncertainBtn">查看消息</button>
          <button class="btn btn-ghost btn-sm" id="msgForceRetrySendBtn">仍然重新发送</button>
        `;
      }

      const uncertainTitle = sr.kind === "image" ? "图片发送结果未知" : sr.kind === "file" ? "文件发送结果未知" : "发送结果未知";
      const uncertainText = sr.kind === "image"
        ? "微信可能已经收到这张图片。为避免重复发送，系统没有自动重试。"
        : sr.kind === "file"
          ? "微信可能已经收到该文件。为避免重复发送，系统没有自动重试。"
          : "微信可能已经收到这条消息。为避免重复发送，系统没有自动重试。";

      sendStatusBannerHtml = `
        <div class="send-result" data-state="uncertain">
          <div style="color: var(--warning); display: inline-flex;">${icon("alertTriangle")}</div>
          <div class="send-result-body">
            <div class="send-result-title">${uncertainTitle}</div>
            <div class="send-result-text">${escapeHtml(uncertainText)}</div>
            <div class="send-result-actions">
              ${uncertainActionsHtml}
            </div>
          </div>
        </div>
      `;
    }
  }

  // Composer capability check
  const sendDisabled = caps.canSendText === false || !selectedChat;
  const sendDisabledReason = caps.sendDisabledReason || (!selectedChat ? "请先选择一个会话" : "");

  // Record previous scroll metrics before DOM update
  const prevThread = container.querySelector("#messagesThreadRoot");
  let prevScrollTop = 0;
  let prevScrollHeight = 0;
  let prevClientHeight = 0;
  let wasNearBottom = true;
  if (prevThread) {
    prevScrollTop = prevThread.scrollTop;
    prevScrollHeight = prevThread.scrollHeight;
    prevClientHeight = prevThread.clientHeight;
    wasNearBottom = prevScrollHeight - prevScrollTop - prevClientHeight <= 60;
  }

  container.innerHTML = `
    <div class="page-inner wide">
      <div class="page-head">
        <div>
          <div class="page-title">消息</div>
          <p class="page-subtitle">查看并回复已同步的微信消息。</p>
        </div>
        <div class="page-head-actions">
          <select class="select" id="messagesAccountSwitcher" aria-label="切换微信账号">
            ${accountSwitcherOptions}
          </select>
        </div>
      </div>

      <div class="surface surface-flush">
        <div class="split" data-pane="${mobilePane}">
          <!-- Left Column: Chats List -->
          <div class="split-side">
            <div style="padding: 12px 16px; border-bottom: 1px solid var(--border)">
              <div class="search">
                ${icon("search", { size: "sm" })}
                <input class="input" id="messagesChatSearchInput" placeholder="搜索会话" value="${escapeAttr(
                  state.messageQuery || ""
                )}" />
              </div>
            </div>
            <div class="chat-list scroll-y" id="messagesChatListRoot">${chatListHtml}</div>
          </div>

          <!-- Right Column: Conversation Thread & Composer -->
          <div class="split-main">
            <div class="chat-toolbar">
              <button class="btn btn-icon btn-sm split-back" id="messagesMobileBackBtn" aria-label="返回会话列表">
                ${icon("chevronLeft", { size: "sm" })}
              </button>
              <div class="chat-toolbar-title">
                <div class="item-title" style="display: flex; align-items: center; gap: 8px;">
                  <span>${escapeHtml(selectedChatName)}</span>
                  ${selectedIsGroup ? `<span class="badge" style="font-size: 11px; padding: 2px 6px;">群聊${selectedChat?.member_count ? ` (${selectedChat.member_count}人)` : ""}</span>` : ""}
                </div>
                <div class="caption">${escapeHtml(
                  selectedChat
                    ? `${selectedIsGroup ? "群聊" : "单聊"} · ${messagesToDisplay.length} 条已同步消息`
                    : "选择会话查看"
                )}</div>
              </div>
              <select class="select" id="messagesTypeFilterSelect" style="width: auto; height: var(--control-h-sm); font-size: var(--fs-caption);" aria-label="按类型筛选">
                <option value="" ${state.selectedMessageType === "" ? "selected" : ""}>全部类型</option>
                <option value="text" ${state.selectedMessageType === "text" ? "selected" : ""}>文字</option>
                <option value="image" ${state.selectedMessageType === "image" ? "selected" : ""}>图片</option>
                <option value="file" ${state.selectedMessageType === "file" ? "selected" : ""}>文件</option>
              </select>
            </div>

            ${sendStatusBannerHtml ? `<div style="padding: 12px 16px 0;">${sendStatusBannerHtml}</div>` : ""}

            <div class="thread scroll-y" id="messagesThreadRoot">${threadHtml}</div>

            <div class="composer">
              ${
                caps.canSendImage || caps.canSendFile
                  ? `<div class="composer-tools">
                      ${
                        caps.canSendImage
                          ? `<button class="btn btn-icon btn-sm" id="composerImageBtn" aria-label="发送图片" ${sendDisabled ? "disabled" : ""}>${icon("image", { size: "sm" })}</button>
                             <input type="file" id="composerImageInput" accept="image/*" style="display: none;" />`
                          : ""
                      }
                      ${
                        caps.canSendFile
                          ? `<button class="btn btn-icon btn-sm" id="composerFileBtn" aria-label="发送文件" ${sendDisabled ? "disabled" : ""}>${icon("file", { size: "sm" })}</button>
                             <input type="file" id="composerFileInput" style="display: none;" />`
                          : ""
                      }
                    </div>`
                  : ""
              }
              <div class="composer-input-row">
                <textarea
                  class="textarea"
                  id="composerTextarea"
                  rows="1"
                  placeholder="${sendDisabled ? escapeAttr(sendDisabledReason) : "输入消息…"}"
                  ${sendDisabled ? "disabled" : ""}
                ></textarea>
                <button class="btn btn-primary" id="composerSendBtn" ${sendDisabled ? "disabled" : ""}>发送</button>
              </div>
              ${
                sendDisabled && sendDisabledReason
                  ? `<div class="composer-note caption" style="color: var(--text-tertiary); margin-top: 6px;">${escapeHtml(
                      sendDisabledReason
                    )}</div>`
                  : ""
              }
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  // Precise auto-scroll & visual scroll anchoring
  const threadEl = container.querySelector("#messagesThreadRoot");
  if (threadEl) {
    if (isPrepend) {
      const newScrollHeight = threadEl.scrollHeight;
      threadEl.scrollTop = oldScrollTop + (newScrollHeight - oldScrollHeight);
    } else if (isNewChat || justSent) {
      threadEl.scrollTop = threadEl.scrollHeight;
    } else if (wasNearBottom) {
      threadEl.scrollTop = threadEl.scrollHeight;
    } else {
      // User is reading history -> preserve scroll position
      threadEl.scrollTop = prevScrollTop;
    }
  }

  // Wire Account Switcher
  const switcher = container.querySelector("#messagesAccountSwitcher");
  if (switcher) {
    switcher.onchange = async () => {
      state.activeAccountId = switcher.value;
      state.selectedChatId = "";
      activeChatKey = null;
      chatMessages = [];
      messagesCursor = "";
      mobilePane = "list";
      await reloadData();
    };
  }

  // Wire Search
  const searchInput = container.querySelector("#messagesChatSearchInput");
  if (searchInput) {
    searchInput.oninput = () => {
      state.messageQuery = searchInput.value;
      renderChatListOnly(container, reloadData);
    };
  }

  // Wire Type Filter
  const filterSelect = container.querySelector("#messagesTypeFilterSelect");
  if (filterSelect) {
    filterSelect.onchange = () => {
      state.selectedMessageType = filterSelect.value;
      renderMessagesView(container, reloadData);
    };
  }

  // Wire Mobile Back
  const mobileBackBtn = container.querySelector("#messagesMobileBackBtn");
  if (mobileBackBtn) {
    mobileBackBtn.onclick = () => {
      mobilePane = "list";
      renderMessagesView(container, reloadData);
    };
  }

  // Wire Chat Item clicks
  container.querySelectorAll(".chat-item[data-chat-id]").forEach((item) => {
    item.onclick = () => {
      const newChatId = item.dataset.chatId;
      mobilePane = "detail";
      if (state.selectedChatId !== newChatId) {
        state.selectedChatId = newChatId;
        renderMessagesView(container, reloadData);
      } else {
        renderMessagesView(container, reloadData);
      }
    };
  });

  // Wire Load Older Messages
  const loadOlderBtn = container.querySelector("#messagesLoadOlderBtn");
  if (loadOlderBtn) {
    loadOlderBtn.onclick = async () => {
      if (isLoadingOlder || !messagesCursor) return;
      const currentThreadEl = container.querySelector("#messagesThreadRoot");
      const curScrollTop = currentThreadEl ? currentThreadEl.scrollTop : 0;
      const curScrollHeight = currentThreadEl ? currentThreadEl.scrollHeight : 0;

      isLoadingOlder = true;
      loadOlderBtn.disabled = true;
      loadOlderBtn.textContent = "正在加载更早消息…";

      await fetchScopedMessages(state.activeAccountId, state.selectedChatId, {
        isPrepend: true,
        before: messagesCursor,
        oldScrollTop: curScrollTop,
        oldScrollHeight: curScrollHeight,
      });
      isLoadingOlder = false;
    };
  }


  // Wire Send Button & Enter key
  const textarea = container.querySelector("#composerTextarea");
  const sendBtn = container.querySelector("#composerSendBtn");

  const handleSend = async () => {
    if (!textarea || !selectedChat) return;
    const text = textarea.value.trim();
    if (!text) return;

    const clientRequestId = `console-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const currentActiveAccount = getActiveAccount();
    const payload = {
      account_id: state.activeAccountId,
      chat_id: selectedChat.chat_id,
      text,
      client_request_id: clientRequestId,
      expected_wechat_identity_uuid: currentActiveAccount?.wechat_identity_uuid || "",
    };

    textarea.value = "";
    sendBtn.disabled = true;

    setState({
      sendResult: {
        status: "sending",
        client_request_id: clientRequestId,
        kind: "text",
        text,
      },
    });
    renderMessagesView(container, reloadData);

    try {
      const receipt = await api.sendText(payload, clientRequestId);
      const sendId = receipt.send_id || clientRequestId;
      watchSendStatus(sendId, { kind: "text", text }, container, reloadData);
    } catch (err) {
      setState({
        sendResult: {
          status: "failed",
          error: err.message,
          kind: "text",
          text,
        },
      });
      renderMessagesView(container, reloadData);
    }
  };

  if (sendBtn) sendBtn.onclick = handleSend;
  if (textarea) {
    textarea.onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    };
  }

  // Wire Image Send
  const imageBtn = container.querySelector("#composerImageBtn");
  const imageInput = container.querySelector("#composerImageInput");
  if (imageBtn && imageInput) {
    imageBtn.onclick = () => {
      if (sendDisabled || !selectedChat) return;
      imageInput.click();
    };
    imageInput.onchange = async () => {
      const file = imageInput.files?.[0];
      if (!file || !selectedChat) return;

      const clientRequestId = `console-img-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setState({
        sendResult: {
          status: "sending",
          client_request_id: clientRequestId,
          kind: "image",
          filename: file.name,
          text: `[图片: ${file.name}]`,
        },
      });
      renderMessagesView(container, reloadData);

      try {
        const currentActiveAccount = getActiveAccount();
        const base64 = await readFileAsBase64(file, { imageOnly: true });
        const payload = {
          account_id: state.activeAccountId,
          chat_id: selectedChat.chat_id,
          content_base64: base64,
          filename: file.name,
          mime_type: file.type || "image/jpeg",
          client_request_id: clientRequestId,
          expected_wechat_identity_uuid: currentActiveAccount?.wechat_identity_uuid || "",
        };
        const receipt = await api.sendImage(payload, clientRequestId);
        const sendId = receipt.send_id || clientRequestId;
        watchSendStatus(sendId, { kind: "image", filename: file.name, text: `[图片: ${file.name}]` }, container, reloadData);
      } catch (err) {
        toast({ title: err.message, tone: "bad" });
        setState({
          sendResult: {
            status: "failed",
            error: err.message,
            kind: "image",
            filename: file.name,
            text: `[图片: ${file.name}]`,
          },
        });
        renderMessagesView(container, reloadData);
      } finally {
        imageInput.value = "";
      }
    };
  }

  // Wire File Send
  const fileBtn = container.querySelector("#composerFileBtn");
  const fileInput = container.querySelector("#composerFileInput");
  if (fileBtn && fileInput) {
    fileBtn.onclick = () => {
      if (sendDisabled || !selectedChat) return;
      fileInput.click();
    };
    fileInput.onchange = async () => {
      const file = fileInput.files?.[0];
      if (!file || !selectedChat) return;

      const clientRequestId = `console-file-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setState({
        sendResult: {
          status: "sending",
          client_request_id: clientRequestId,
          kind: "file",
          filename: file.name,
          text: `[文件: ${file.name}]`,
        },
      });
      renderMessagesView(container, reloadData);

      try {
        const currentActiveAccount = getActiveAccount();
        const base64 = await readFileAsBase64(file, { imageOnly: false });
        const payload = {
          account_id: state.activeAccountId,
          chat_id: selectedChat.chat_id,
          content_base64: base64,
          filename: file.name,
          mime_type: file.type || "application/octet-stream",
          client_request_id: clientRequestId,
          expected_wechat_identity_uuid: currentActiveAccount?.wechat_identity_uuid || "",
        };
        const receipt = await api.sendFile(payload, clientRequestId);
        const sendId = receipt.send_id || clientRequestId;
        watchSendStatus(sendId, { kind: "file", filename: file.name, text: `[文件: ${file.name}]` }, container, reloadData);
      } catch (err) {
        toast({ title: err.message, tone: "bad" });
        setState({
          sendResult: {
            status: "failed",
            error: err.message,
            kind: "file",
            filename: file.name,
            text: `[文件: ${file.name}]`,
          },
        });
        renderMessagesView(container, reloadData);
      } finally {
        fileInput.value = "";
      }
    };
  }

  // Wire send result actions
  const retrySendBtn = container.querySelector("#msgRetrySendBtn");
  if (retrySendBtn) {
    retrySendBtn.onclick = () => {
      const textToRetry = state.sendResult?.text || "";
      setState({ sendResult: null });
      renderMessagesView(container, reloadData);
      const newTextarea = container.querySelector("#composerTextarea");
      if (newTextarea) {
        newTextarea.value = textToRetry;
        newTextarea.focus();
      }
    };
  }

  const retryImageBtn = container.querySelector("#msgRetryImageBtn");
  if (retryImageBtn) {
    retryImageBtn.onclick = () => {
      setState({ sendResult: null });
      renderMessagesView(container, reloadData);
      const newImgInput = container.querySelector("#composerImageInput");
      newImgInput?.click();
    };
  }

  const retryFileBtn = container.querySelector("#msgRetryFileBtn");
  if (retryFileBtn) {
    retryFileBtn.onclick = () => {
      setState({ sendResult: null });
      renderMessagesView(container, reloadData);
      const newFileInput = container.querySelector("#composerFileInput");
      newFileInput?.click();
    };
  }

  const dismissUncertainBtn = container.querySelector("#msgDismissUncertainBtn");
  if (dismissUncertainBtn) {
    dismissUncertainBtn.onclick = () => {
      setState({ sendResult: null });
      renderMessagesView(container, reloadData);
    };
  }

  const forceRetryBtn = container.querySelector("#msgForceRetrySendBtn");
  if (forceRetryBtn && state.sendResult?.text) {
    forceRetryBtn.onclick = async () => {
      const confirmed = await confirmAction({
        title: "仍然重新发送？",
        text: "这条消息可能已经发出。再发一次可能让对方收到两条相同消息。",
        confirmLabel: "仍然发送",
        tone: "danger",
      });
      if (!confirmed) return;

      const textToRetry = state.sendResult.text;
      setState({ sendResult: null });
      if (textarea) textarea.value = textToRetry;
      handleSend();
    };
  }

  const forceRetryImageBtn = container.querySelector("#msgForceRetryImageBtn");
  if (forceRetryImageBtn) {
    forceRetryImageBtn.onclick = async () => {
      const confirmed = await confirmAction({
        title: "重新选择图片？",
        text: "该图片可能已经成功送达对方。再次发送可能导致对方收到重复图片。",
        confirmLabel: "重新选择",
        tone: "danger",
      });
      if (!confirmed) return;

      setState({ sendResult: null });
      renderMessagesView(container, reloadData);
      const newImgInput = container.querySelector("#composerImageInput");
      newImgInput?.click();
    };
  }

  const forceRetryFileBtn = container.querySelector("#msgForceRetryFileBtn");
  if (forceRetryFileBtn) {
    forceRetryFileBtn.onclick = async () => {
      const confirmed = await confirmAction({
        title: "重新选择文件？",
        text: "该文件可能已经成功送达对方。再次发送可能导致对方收到重复文件。",
        confirmLabel: "重新选择",
        tone: "danger",
      });
      if (!confirmed) return;

      setState({ sendResult: null });
      renderMessagesView(container, reloadData);
      const newFileInput = container.querySelector("#composerFileInput");
      newFileInput?.click();
    };
  }

  // Wire Save buttons
  container.querySelectorAll("button[data-save-msg]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const msgId = btn.dataset.saveMsg;
      const msg = chatMessages.find((m) => m.message_id === msgId);
      if (msg) openSaveDialog(msg, reloadData);
    };
  });
}

function watchSendStatus(sendId, payloadInfo, container, reloadData) {
  if (activeSendWatcher) {
    clearTimeout(activeSendWatcher);
    activeSendWatcher = null;
  }

  const kind = typeof payloadInfo === "object" ? payloadInfo.kind || "text" : "text";
  const text = typeof payloadInfo === "object" ? payloadInfo.text : payloadInfo;
  const filename = typeof payloadInfo === "object" ? payloadInfo.filename : null;

  let pollCount = 0;
  const maxPolls = 90;

  const check = async () => {
    pollCount++;
    try {
      const send = await api.sendStatus(sendId);
      const status = send.status;

      setState({
        sendResult: {
          send_id: sendId,
          status,
          delivery_certainty: send.delivery_certainty,
          automatic_retry: send.automatic_retry,
          echo_message_id: send.echo_message_id,
          error: send.error,
          kind,
          text,
          filename,
        },
      });

      renderMessagesView(container, reloadData);

      if (status === "sent" || status === "uncertain" || status === "failed") {
        if (status === "sent") {
          // Fetch latest messages for this chat and scroll to bottom
          if (state.activeAccountId && state.selectedChatId) {
            await fetchScopedMessages(state.activeAccountId, state.selectedChatId, { isNewChat: false });
          }
          renderMessagesView(container, reloadData, { justSent: true });
        } else {
          await reloadData();
        }
        return;
      }

      if (pollCount < maxPolls) {
        const delay = pollCount === 1 ? 500 : 2000;
        activeSendWatcher = setTimeout(check, delay);
      }
    } catch (err) {
      console.warn("Send status check failed:", err);
      if (pollCount < maxPolls) {
        activeSendWatcher = setTimeout(check, 2000);
      }
    }
  };

  activeSendWatcher = setTimeout(check, 500);
}

function openSaveDialog(message, reloadData) {
  let dialog = saveDialogEl;
  if (!dialog || !document.body.contains(dialog)) {
    dialog = document.getElementById("saveMessageModal");
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "saveMessageModal";
      dialog.className = "modal";
      document.body.appendChild(dialog);
    }
    saveDialogEl = dialog;
  }

  const initialTitle = message.text
    ? message.text.slice(0, 30)
    : message.filename || "收藏消息";

  dialog.innerHTML = `
    <div class="modal-shell">
      <div class="modal-head">
        <div class="modal-head-text">
          <div class="modal-title">添加到收藏</div>
          <p class="modal-subtitle">保存消息快照并添加注释与标签。</p>
        </div>
        <button class="btn btn-icon" id="saveDialogCloseBtn" aria-label="关闭">
          ${icon("close")}
        </button>
      </div>
      <div class="modal-body">
        <div class="snapshot" style="margin-bottom: 16px;">
          <strong>${escapeHtml(message.sender_name || message.sender || "发送者")}</strong>
          <span>${escapeHtml(message.text || message.filename || "[媒体内容]")}</span>
          <span class="caption mono">${escapeHtml(message.type || "text")} · ${fmtDateTime(
    message.timestamp || message.created_at
  )}</span>
        </div>
        <div class="field">
          <label class="label" for="saveTitleInput">标题</label>
          <input class="input" id="saveTitleInput" value="${escapeAttr(initialTitle)}" />
        </div>
        <div class="field">
          <label class="label" for="saveTagsInput">标签</label>
          <input class="input" id="saveTagsInput" placeholder="例如：工作, 报销, 待办" />
          <p class="field-hint">多个标签用逗号分隔。</p>
        </div>
        <div class="field">
          <label class="label" for="saveNoteInput">注释</label>
          <textarea class="textarea" id="saveNoteInput" rows="3" placeholder="添加备注或说明…"></textarea>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" id="saveDialogCancelBtn">取消</button>
        <button class="btn btn-primary" id="saveDialogSubmitBtn">保存到收藏</button>
      </div>
    </div>
  `;

  dialog.querySelector("#saveDialogCloseBtn").onclick = () => closeDialog(dialog);
  dialog.querySelector("#saveDialogCancelBtn").onclick = () => closeDialog(dialog);

  const submitBtn = dialog.querySelector("#saveDialogSubmitBtn");
  submitBtn.onclick = async () => {
    submitBtn.disabled = true;
    const title = dialog.querySelector("#saveTitleInput").value.trim();
    const tagsRaw = dialog.querySelector("#saveTagsInput").value;
    const tags = tagsRaw
      .split(/[,，]/)
      .map((t) => t.trim())
      .filter(Boolean);
    const note = dialog.querySelector("#saveNoteInput").value.trim();

    try {
      await api.saveMessage({
        account_id: message.account_id || state.activeAccountId,
        chat_id: message.chat_id,
        message_id: message.message_id,
        title: title || initialTitle,
        tags,
        note,
      });
      toast({ title: "已保存到收藏", tone: "good" });
      closeDialog(dialog);
      await reloadData();
    } catch (err) {
      toast({ title: "保存失败", text: err.message, tone: "bad" });
      submitBtn.disabled = false;
    }
  };

  openDialog(dialog);
}
