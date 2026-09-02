/* Messages View.
 *
 * Dual-pane chat browser, capability-aware message composer,
 * send status tracking (submitted, sent, uncertain, failed),
 * and saved messages integration.
 */

import { state, setState } from "../state.js";
import { api } from "../api.js";
import { capabilitiesOf } from "../capabilities.js";
import { escapeHtml, escapeAttr, fmtWhen, fmtDateTime, initial } from "../format.js";
import { icon } from "../icons.js";
import { confirmAction } from "../components/confirm.js";
import { toast } from "../components/toast.js";
import { openDialog, closeDialog } from "../components/dialog.js";

let saveDialogEl = null;
let activeSendWatcher = null;
let mobilePane = "list"; // "list" | "detail"

function readFileAsBase64(file, { imageOnly = false } = {}) {
  return new Promise((resolve, reject) => {
    if (imageOnly && !file.type.startsWith("image/")) {
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
 */
export function renderMessagesView(container, reloadData) {
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
  const chatQuery = (state.messageQuery || "").trim().toLowerCase();
  const allChats = state.chats || [];
  const filteredChats = chatQuery
    ? allChats.filter((c) =>
        (c.name || c.chat_id || "").toLowerCase().includes(chatQuery)
      )
    : allChats;

  if (!state.selectedChatId && filteredChats.length > 0) {
    state.selectedChatId = filteredChats[0].chat_id;
  }

  const selectedChat = allChats.find((c) => c.chat_id === state.selectedChatId) || filteredChats[0] || null;

  // Account Switcher options
  const accountSwitcherOptions = accounts
    .map(
      (a) =>
        `<option value="${escapeAttr(a.account_id)}" ${
          a.account_id === state.activeAccountId ? "selected" : ""
        }>${escapeHtml(a.display_name || a.account_id)}</option>`
    )
    .join("");

  // Render Chat list items
  let chatListHtml = "";
  if (filteredChats.length === 0) {
    chatListHtml = `<div style="padding: 24px 16px; text-align: center; color: var(--text-secondary);">暂无会话</div>`;
  } else {
    chatListHtml = filteredChats
      .map((c) => {
        const isSelected = c.chat_id === state.selectedChatId;
        const name = c.name || c.chat_id || "未命名会话";
        const initialGlyph = initial(name, "会");
        const snippet = c.last_message_snippet || c.last_message || "";

        return `
          <button class="chat-item" data-chat-id="${escapeAttr(
            c.chat_id
          )}" aria-selected="${isSelected ? "true" : "false"}">
            <span class="avatar avatar-sm">${escapeHtml(initialGlyph)}</span>
            <span class="chat-item-body">
              <span class="chat-item-name">${escapeHtml(name)}</span>
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

  // Filter messages for the selected chat
  let messagesForChat = (state.messages || []).filter(
    (m) =>
      (!state.activeAccountId || m.account_id === state.activeAccountId) &&
      (!state.selectedChatId || m.chat_id === state.selectedChatId)
  );

  if (state.selectedMessageType) {
    messagesForChat = messagesForChat.filter(
      (m) => m.type === state.selectedMessageType
    );
  }

  // Render Message Thread
  let threadHtml = "";
  if (!selectedChat) {
    threadHtml = `
      <div class="empty" style="padding: 48px var(--gutter);">
        <div class="empty-icon">${icon("message")}</div>
        <div class="empty-title">选择一个会话</div>
        <p class="empty-text">在左侧列表选择会话查看同步的消息。</p>
      </div>
    `;
  } else if (messagesForChat.length === 0) {
    threadHtml = `
      <div class="empty" style="padding: 48px var(--gutter);">
        <div class="empty-icon">${icon("message")}</div>
        <div class="empty-title">暂无消息</div>
        <p class="empty-text">该会话尚未同步到消息记录。</p>
      </div>
    `;
  } else {
    threadHtml = messagesForChat
      .map((m) => {
        const isOutgoing = Boolean(m.is_sender || m.is_outgoing || m.from_user === "me");
        const authorName = isOutgoing ? "我" : (m.sender_name || m.sender || selectedChat.name || "对方");
        const when = fmtWhen(m.timestamp || m.created_at || m.occurred_at);
        const authorText = `${authorName} · ${when}`;

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

        const isRevoked = Boolean(m.revoked || m.is_revoked);
        const textHtml = isRevoked
          ? `<em style="color: var(--text-tertiary);">[消息已撤回/移除]</em>`
          : escapeHtml(m.text || "");

        return `
          <div class="bubble-line ${isOutgoing ? "outgoing" : ""}" data-msg-id="${escapeAttr(
            m.message_id || ""
          )}">
            <span class="bubble-author">${escapeHtml(authorText)}</span>
            <div class="bubble">
              ${textHtml ? `<div class="bubble-text">${textHtml}</div>` : ""}
              ${attachmentHtml}
              <button class="btn btn-icon btn-sm bubble-save-btn" data-save-msg="${escapeAttr(
                m.message_id || ""
              )}" aria-label="收藏此消息" title="收藏此消息">
                ${icon("star", { size: "sm" })}
              </button>
            </div>
            ${
              isOutgoing
                ? `<div class="bubble-foot"><span>已发送</span></div>`
                : ""
            }
          </div>
        `;
      })
      .join("");
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
      sendStatusBannerHtml = `
        <div class="send-result" data-state="failed">
          <div style="color: var(--danger); display: inline-flex;">${icon("alertCircle")}</div>
          <div class="send-result-body">
            <div class="send-result-title">发送失败</div>
            <div class="send-result-text">${escapeHtml(sr.error || "微信没有接收这条消息，可以重新发送。")}</div>
            <div class="send-result-actions">
              <button class="btn btn-secondary btn-sm" id="msgRetrySendBtn">重新发送</button>
            </div>
          </div>
        </div>
      `;
    } else if (sr.status === "uncertain") {
      sendStatusBannerHtml = `
        <div class="send-result" data-state="uncertain">
          <div style="color: var(--warning); display: inline-flex;">${icon("alertTriangle")}</div>
          <div class="send-result-body">
            <div class="send-result-title">发送结果未知</div>
            <div class="send-result-text">微信可能已经收到这条消息。为避免重复发送，系统没有自动重试。</div>
            <div class="send-result-actions">
              <button class="btn btn-secondary btn-sm" id="msgDismissUncertainBtn">查看消息</button>
              <button class="btn btn-ghost btn-sm" id="msgForceRetrySendBtn">仍然重新发送</button>
            </div>
          </div>
        </div>
      `;
    }
  }

  // Composer capability check
  const sendDisabled = caps.canSendText === false || !selectedChat;
  const sendDisabledReason = caps.sendDisabledReason || (!selectedChat ? "请先选择一个会话" : "");

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
                <div class="item-title">${escapeHtml(
                  selectedChat?.name || selectedChat?.chat_id || "未选择会话"
                )}</div>
                <div class="caption">${escapeHtml(
                  selectedChat
                    ? `${selectedChat.is_group ? "群聊" : "单聊"} · ${messagesForChat.length} 条已同步消息`
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

  // Auto scroll thread to bottom
  const threadEl = container.querySelector("#messagesThreadRoot");
  if (threadEl) {
    threadEl.scrollTop = threadEl.scrollHeight;
  }

  // Wire Account Switcher
  const switcher = container.querySelector("#messagesAccountSwitcher");
  if (switcher) {
    switcher.onchange = async () => {
      state.activeAccountId = switcher.value;
      state.selectedChatId = "";
      mobilePane = "list";
      await reloadData();
    };
  }

  // Wire Search
  const searchInput = container.querySelector("#messagesChatSearchInput");
  if (searchInput) {
    searchInput.oninput = () => {
      state.messageQuery = searchInput.value;
      renderMessagesView(container, reloadData);
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
      state.selectedChatId = item.dataset.chatId;
      mobilePane = "detail";
      renderMessagesView(container, reloadData);
    };
  });

  // Wire Send Button & Enter key
  const textarea = container.querySelector("#composerTextarea");
  const sendBtn = container.querySelector("#composerSendBtn");

  const handleSend = async () => {
    if (!textarea || !selectedChat) return;
    const text = textarea.value.trim();
    if (!text) return;

    const clientRequestId = `console-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const payload = {
      account_id: state.activeAccountId,
      chat_id: selectedChat.chat_id,
      text,
      client_request_id: clientRequestId,
    };

    textarea.value = "";
    sendBtn.disabled = true;

    setState({
      sendResult: {
        status: "sending",
        client_request_id: clientRequestId,
        text,
      },
    });
    renderMessagesView(container, reloadData);

    try {
      const receipt = await api.sendText(payload, clientRequestId);
      const sendId = receipt.send_id || clientRequestId;
      watchSendStatus(sendId, text, container, reloadData);
    } catch (err) {
      setState({
        sendResult: {
          status: "failed",
          error: err.message,
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
          text: `[图片: ${file.name}]`,
        },
      });
      renderMessagesView(container, reloadData);

      try {
        const base64 = await readFileAsBase64(file, { imageOnly: true });
        const payload = {
          account_id: state.activeAccountId,
          chat_id: selectedChat.chat_id,
          content_base64: base64,
          filename: file.name,
          mime_type: file.type || "image/jpeg",
          client_request_id: clientRequestId,
        };
        const receipt = await api.sendImage(payload, clientRequestId);
        const sendId = receipt.send_id || clientRequestId;
        watchSendStatus(sendId, `[图片: ${file.name}]`, container, reloadData);
      } catch (err) {
        toast(err.message, "bad");
        setState({
          sendResult: {
            status: "failed",
            error: err.message,
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
          text: `[文件: ${file.name}]`,
        },
      });
      renderMessagesView(container, reloadData);

      try {
        const base64 = await readFileAsBase64(file, { imageOnly: false });
        const payload = {
          account_id: state.activeAccountId,
          chat_id: selectedChat.chat_id,
          content_base64: base64,
          filename: file.name,
          mime_type: file.type || "application/octet-stream",
          client_request_id: clientRequestId,
        };
        const receipt = await api.sendFile(payload, clientRequestId);
        const sendId = receipt.send_id || clientRequestId;
        watchSendStatus(sendId, `[文件: ${file.name}]`, container, reloadData);
      } catch (err) {
        toast(err.message, "bad");
        setState({
          sendResult: {
            status: "failed",
            error: err.message,
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
  if (retrySendBtn && state.sendResult?.text) {
    retrySendBtn.onclick = () => {
      if (textarea) textarea.value = state.sendResult.text;
      setState({ sendResult: null });
      renderMessagesView(container, reloadData);
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

  // Wire Save buttons
  container.querySelectorAll("button[data-save-msg]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const msgId = btn.dataset.saveMsg;
      const msg = messagesForChat.find((m) => m.message_id === msgId);
      if (msg) openSaveDialog(msg, reloadData);
    };
  });
}

function watchSendStatus(sendId, text, container, reloadData) {
  if (activeSendWatcher) {
    clearTimeout(activeSendWatcher);
    activeSendWatcher = null;
  }

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
          text,
        },
      });

      renderMessagesView(container, reloadData);

      if (status === "sent" || status === "uncertain" || status === "failed") {
        await reloadData();
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
