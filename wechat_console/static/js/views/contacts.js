/* Contacts & Groups View (v2).
 *
 * Contact list, group chat browser, group member inspector,
 * multi-tier display name hierarchy, safe avatar rendering,
 * and seamless navigation to Messages.
 */

import { state, setState } from "../state.js";
import { api } from "../api.js";
import { escapeHtml, escapeAttr, initial } from "../format.js";
import { icon } from "../icons.js";
import { navigate } from "../router.js";

let activeTab = "contacts"; // "contacts" | "groups"
let mobilePane = "list"; // "list" | "detail"
let contactsList = [];
let contactsCursor = "";
let contactsHasMore = false;
let isLoadingContacts = false;
let selectedMemberId = "";
let selectedChatId = "";
let groupMembers = [];
let groupMembersCursor = "";
let groupMembersHasMore = false;
let isLoadingMembers = false;
let searchQuery = "";
let memberSearchQuery = "";
let lastAccountId = "";

function renderAvatar(name, avatarUrl, size = "md", isSelf = false) {
  const sizePx = size === "lg" ? 56 : size === "sm" ? 28 : 36;
  const radius = size === "lg" ? "var(--r-md)" : "var(--r-sm)";
  const fontSize = size === "lg" ? "22px" : size === "sm" ? "11px" : "14px";
  if (avatarUrl) {
    return `<img class="avatar avatar-${size}" src="${escapeAttr(
      avatarUrl
    )}" alt="${escapeAttr(name)}" style="width: ${sizePx}px; height: ${sizePx}px; border-radius: ${radius}; object-fit: cover;" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" /><span class="avatar avatar-${size}" style="display: none; width: ${sizePx}px; height: ${sizePx}px; font-size: ${fontSize};">${escapeHtml(
      initial(name, isSelf ? "我" : "友")
    )}</span>`;
  }
  const glyph = initial(name, isSelf ? "我" : "友");
  return `<span class="avatar avatar-${size}" style="width: ${sizePx}px; height: ${sizePx}px; font-size: ${fontSize};">${escapeHtml(
    glyph
  )}</span>`;
}

async function loadContacts(accountId, { append = false, reset = false } = {}) {
  if (!accountId) return;
  if (reset) {
    contactsList = [];
    contactsCursor = "";
    contactsHasMore = false;
  }
  isLoadingContacts = true;
  try {
    const params = {
      account_id: accountId,
      limit: 100,
    };
    if (searchQuery) params.query = searchQuery;
    if (append && contactsCursor) params.cursor = contactsCursor;

    const res = await api.contacts(params);
    const fetched = res.contacts || [];
    contactsCursor = res.next_cursor || "";
    contactsHasMore = Boolean(res.has_more);

    if (append) {
      contactsList = [...contactsList, ...fetched];
    } else {
      contactsList = fetched;
      if (!selectedMemberId && contactsList.length > 0) {
        selectedMemberId = contactsList[0].member_id;
      }
    }
  } catch (err) {
    console.error("Failed to load contacts:", err);
  } finally {
    isLoadingContacts = false;
  }
}

async function loadGroupMembers(accountId, chatId, { append = false, reset = false } = {}) {
  if (!accountId || !chatId) return;
  if (reset) {
    groupMembers = [];
    groupMembersCursor = "";
    groupMembersHasMore = false;
  }
  isLoadingMembers = true;
  try {
    const params = {
      account_id: accountId,
      limit: 200,
    };
    if (memberSearchQuery) params.query = memberSearchQuery;
    if (append && groupMembersCursor) params.cursor = groupMembersCursor;

    const res = await api.groupMembers(chatId, params);
    const fetched = res.members || [];
    groupMembersCursor = res.next_cursor || "";
    groupMembersHasMore = Boolean(res.has_more);

    if (append) {
      groupMembers = [...groupMembers, ...fetched];
    } else {
      groupMembers = fetched;
    }
  } catch (err) {
    console.error("Failed to load group members:", err);
  } finally {
    isLoadingMembers = false;
  }
}

export async function renderContactsView(container, reloadData) {
  const accounts = state.runtimeAccounts.length > 0 ? state.runtimeAccounts : state.accounts;
  if (!state.activeAccountId && accounts.length > 0) {
    state.activeAccountId = accounts[0].account_id;
  }

  const currentAccountId = state.activeAccountId;
  if (currentAccountId !== lastAccountId) {
    lastAccountId = currentAccountId;
    selectedMemberId = "";
    selectedChatId = "";
    contactsList = [];
    groupMembers = [];
    await loadContacts(currentAccountId, { reset: true });
  } else if (contactsList.length === 0 && !isLoadingContacts) {
    await loadContacts(currentAccountId, { reset: true });
  }

  const allChats = state.chats || [];
  const groupChats = allChats.filter(
    (c) => c.type === "group" || Boolean(c.is_group) || (c.chat_id && c.chat_id.endsWith("@chatroom"))
  );

  if (activeTab === "groups" && !selectedChatId && groupChats.length > 0) {
    selectedChatId = groupChats[0].chat_id;
    await loadGroupMembers(currentAccountId, selectedChatId, { reset: true });
  }

  // Account selector options
  const accountOptions = accounts
    .map(
      (a) =>
        `<option value="${escapeAttr(a.account_id)}" ${
          a.account_id === currentAccountId ? "selected" : ""
        }>${escapeHtml(a.display_name || a.account_id)}</option>`
    )
    .join("");

  // Contact list HTML
  let leftListHtml = "";
  if (activeTab === "contacts") {
    if (isLoadingContacts && contactsList.length === 0) {
      leftListHtml = `<div style="padding: 32px 16px; text-align: center; color: var(--text-secondary);">正在加载联系人…</div>`;
    } else if (contactsList.length === 0) {
      leftListHtml = `<div style="padding: 32px 16px; text-align: center; color: var(--text-secondary);">暂无联系人</div>`;
    } else {
      const itemsHtml = contactsList
        .map((c) => {
          const isSelected = c.member_id === selectedMemberId;
          const name = c.display_name || c.nickname || c.alias || c.member_id;
          const sub = c.remark && c.nickname && c.remark !== c.nickname ? `昵称: ${c.nickname}` : c.alias ? `微信号: ${c.alias}` : c.member_id;
          return `
            <button class="chat-item" data-contact-id="${escapeAttr(c.member_id)}" aria-selected="${isSelected ? "true" : "false"}">
              ${renderAvatar(name, c.avatar_url || c.avatar_ref, "sm")}
              <span class="chat-item-body">
                <span class="chat-item-name">${escapeHtml(name)}</span>
                <span class="chat-item-meta">${escapeHtml(sub)}</span>
              </span>
            </button>
          `;
        })
        .join("");

      const moreBtn = contactsHasMore
        ? `<div style="padding: 8px 12px; text-align: center;">
             <button class="btn btn-secondary btn-sm" id="loadMoreContactsBtn" ${isLoadingContacts ? "disabled" : ""}>
               ${isLoadingContacts ? "加载中…" : "加载更多联系人"}
             </button>
           </div>`
        : "";

      leftListHtml = `${itemsHtml}${moreBtn}`;
    }
  } else {
    // Groups tab
    const filteredGroups = searchQuery
      ? groupChats.filter((g) => {
          const q = searchQuery.toLowerCase();
          return (
            (g.display_name || g.name || "").toLowerCase().includes(q) ||
            (g.chat_id || "").toLowerCase().includes(q)
          );
        })
      : groupChats;

    if (filteredGroups.length === 0) {
      leftListHtml = `<div style="padding: 32px 16px; text-align: center; color: var(--text-secondary);">暂无群聊</div>`;
    } else {
      leftListHtml = filteredGroups
        .map((g) => {
          const isSelected = g.chat_id === selectedChatId;
          const name = g.display_name || g.name || g.chat_id;
          return `
            <button class="chat-item" data-group-id="${escapeAttr(g.chat_id)}" aria-selected="${isSelected ? "true" : "false"}">
              ${renderAvatar(name, "", "sm")}
              <span class="chat-item-body">
                <span class="chat-item-name" style="display: flex; align-items: center; justify-content: space-between;">
                  <span>${escapeHtml(name)}</span>
                  ${g.member_count ? `<span class="badge" style="font-size: 10px;">${g.member_count}人</span>` : ""}
                </span>
                <span class="chat-item-meta">${escapeHtml(g.chat_id)}</span>
              </span>
            </button>
          `;
        })
        .join("");
    }
  }

  // Detail Pane HTML
  let rightDetailHtml = "";
  if (activeTab === "contacts") {
    const selectedContact = contactsList.find((c) => c.member_id === selectedMemberId);
    if (!selectedContact) {
      rightDetailHtml = `
        <div class="empty" style="padding: 48px var(--gutter);">
          <div class="empty-icon">${icon("users")}</div>
          <div class="empty-title">选择联系人</div>
          <p class="empty-text">在左侧列表中选择联系人查看详情。</p>
        </div>
      `;
    } else {
      const name = selectedContact.display_name || selectedContact.nickname || selectedContact.member_id;
      rightDetailHtml = `
        <div class="contact-detail-wrap" style="padding: 24px; max-width: 680px; width: 100%;">
          <div class="card" style="padding: 24px; margin-bottom: 20px;">
            <div style="display: flex; align-items: center; gap: 20px; margin-bottom: 20px;">
              ${renderAvatar(name, selectedContact.avatar_url || selectedContact.avatar_ref, "lg")}
              <div style="flex: 1; min-width: 0;">
                <h2 style="margin: 0 0 6px 0; font-size: 20px; font-weight: 600;">${escapeHtml(name)}</h2>
                ${selectedContact.remark && selectedContact.nickname && selectedContact.remark !== selectedContact.nickname ? `<div style="font-size: 13px; color: var(--text-secondary);">昵称: ${escapeHtml(selectedContact.nickname)}</div>` : ""}
                ${selectedContact.alias ? `<div style="font-size: 13px; color: var(--text-tertiary);">微信号: ${escapeHtml(selectedContact.alias)}</div>` : ""}
              </div>
              <button class="btn btn-primary btn-sm" id="gotoChatBtn" style="flex-shrink: 0;">
                ${icon("message", { size: "sm" })}
                <span>发起聊天</span>
              </button>
            </div>
            <div class="contact-fields" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; padding-top: 16px; border-top: 1px solid var(--border);">
              <div>
                <span class="caption" style="display: block; margin-bottom: 4px; color: var(--text-tertiary);">备注名</span>
                <span style="font-size: 14px;">${escapeHtml(selectedContact.remark || "—")}</span>
              </div>
              <div>
                <span class="caption" style="display: block; margin-bottom: 4px; color: var(--text-tertiary);">微信昵称</span>
                <span style="font-size: 14px;">${escapeHtml(selectedContact.nickname || "—")}</span>
              </div>
              <div>
                <span class="caption" style="display: block; margin-bottom: 4px; color: var(--text-tertiary);">微信号 (Alias)</span>
                <span style="font-size: 14px;">${escapeHtml(selectedContact.alias || "—")}</span>
              </div>
              <div>
                <span class="caption" style="display: block; margin-bottom: 4px; color: var(--text-tertiary);">微信账号标识 (wxid)</span>
                <span style="font-size: 14px; font-family: var(--font-mono); color: var(--text-secondary);">${escapeHtml(selectedContact.member_id)}</span>
              </div>
            </div>
          </div>
        </div>
      `;
    }
  } else {
    // Groups detail pane
    const selectedGroup = groupChats.find((g) => g.chat_id === selectedChatId);
    if (!selectedGroup) {
      rightDetailHtml = `
        <div class="empty" style="padding: 48px var(--gutter);">
          <div class="empty-icon">${icon("users")}</div>
          <div class="empty-title">选择群聊</div>
          <p class="empty-text">在左侧列表中选择群聊查看成员详情。</p>
        </div>
      `;
    } else {
      const groupName = selectedGroup.display_name || selectedGroup.name || selectedGroup.chat_id;
      const membersCountText = groupMembers.length ? `${groupMembers.length} 名成员已同步` : `${selectedGroup.member_count || 0} 人`;

      let membersListHtml = "";
      if (isLoadingMembers && groupMembers.length === 0) {
        membersListHtml = `<div style="padding: 32px; text-align: center; color: var(--text-secondary);">正在加载群成员…</div>`;
      } else if (groupMembers.length === 0) {
        membersListHtml = `<div style="padding: 32px; text-align: center; color: var(--text-secondary);">该群成员尚未同步</div>`;
      } else {
        const memberCardsHtml = groupMembers
          .map((m) => {
            const mName = m.display_name || m.group_nickname || m.nickname || m.member_id;
            const subName = m.group_nickname && m.nickname && m.group_nickname !== m.nickname
              ? `原昵称: ${m.nickname}`
              : m.alias
              ? `微信号: ${m.alias}`
              : m.member_id;

            return `
              <div class="member-card card" style="padding: 12px; display: flex; align-items: center; gap: 12px;">
                ${renderAvatar(mName, m.avatar_url || m.avatar_ref, "md", m.is_self)}
                <div style="flex: 1; min-width: 0;">
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-weight: 500; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(mName)}</span>
                    ${m.is_self ? `<span class="badge" style="background: var(--brand-soft); color: var(--brand); font-size: 10px; padding: 1px 4px;">我</span>` : ""}
                    ${m.group_nickname ? `<span class="badge" style="font-size: 10px; padding: 1px 4px;">群昵称</span>` : ""}
                  </div>
                  <div class="caption" style="color: var(--text-tertiary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(subName)}</div>
                </div>
              </div>
            `;
          })
          .join("");

        const loadMoreMembersBtn = groupMembersHasMore
          ? `<div style="grid-column: 1 / -1; text-align: center; padding: 8px 0;">
               <button class="btn btn-secondary btn-sm" id="loadMoreMembersBtn" ${isLoadingMembers ? "disabled" : ""}>
                 ${isLoadingMembers ? "加载中…" : "加载更多成员"}
               </button>
             </div>`
          : "";

        membersListHtml = `
          <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px;">
            ${memberCardsHtml}
            ${loadMoreMembersBtn}
          </div>
        `;
      }

      rightDetailHtml = `
        <div class="group-detail-wrap" style="padding: 24px; width: 100%; height: 100%; display: flex; flex-direction: column; overflow: hidden;">
          <div class="card" style="padding: 16px 20px; margin-bottom: 16px; flex-shrink: 0;">
            <div style="display: flex; align-items: center; justify-content: space-between; gap: 16px;">
              <div style="display: flex; align-items: center; gap: 14px;">
                ${renderAvatar(groupName, "", "md")}
                <div>
                  <h2 style="margin: 0 0 4px 0; font-size: 18px; font-weight: 600;">${escapeHtml(groupName)}</h2>
                  <div class="caption" style="color: var(--text-tertiary);">${escapeHtml(membersCountText)} · <span style="font-family: var(--font-mono);">${escapeHtml(selectedGroup.chat_id)}</span></div>
                </div>
              </div>
              <button class="btn btn-primary btn-sm" id="gotoGroupChatBtn">
                ${icon("message", { size: "sm" })}
                <span>进入群聊</span>
              </button>
            </div>
          </div>

          <div style="margin-bottom: 12px; display: flex; align-items: center; gap: 10px; flex-shrink: 0;">
            <div class="search-wrap" style="flex: 1; max-width: 320px;">
              <input type="search" class="input input-sm" id="memberSearchInput" placeholder="搜索群成员…" value="${escapeAttr(memberSearchQuery)}" />
            </div>
            <span class="caption" style="color: var(--text-tertiary);">共 ${groupMembers.length} 名成员</span>
          </div>

          <div style="flex: 1; min-height: 0; overflow-y: auto;">
            ${membersListHtml}
          </div>
        </div>
      `;
    }
  }

  container.innerHTML = `
    <div class="page-inner wide">
      <div class="page-head" style="margin-bottom: 16px;">
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 16px;">
          <div>
            <h1 class="page-title">通讯录</h1>
            <p class="page-desc">查看已同步的联系人与群成员，支持多级名称层级与安全头像缓存。</p>
          </div>
          <div style="display: flex; align-items: center; gap: 10px;">
            ${
              accounts.length > 1
                ? `<select class="select select-sm" id="contactsAccountSwitcher">${accountOptions}</select>`
                : ""
            }
            <button class="btn btn-secondary btn-sm btn-icon" id="contactsRefreshBtn" title="刷新通讯录">
              ${icon("refresh", { size: "sm" })}
            </button>
          </div>
        </div>
      </div>

      <div class="split" data-pane="${mobilePane}">
        <!-- Left Pane: List -->
        <div class="split-side" style="display: flex; flex-direction: column; overflow: hidden;">
          <div style="padding: 12px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; flex-shrink: 0;">
            <div style="display: flex; background: var(--bg-tertiary); padding: 2px; border-radius: var(--r-sm);">
              <button class="btn btn-sm ${activeTab === "contacts" ? "btn-secondary" : ""}" id="tabContactsBtn" style="flex: 1; border: none; font-size: 13px; font-weight: ${activeTab === "contacts" ? "600" : "400"};">
                联系人
              </button>
              <button class="btn btn-sm ${activeTab === "groups" ? "btn-secondary" : ""}" id="tabGroupsBtn" style="flex: 1; border: none; font-size: 13px; font-weight: ${activeTab === "groups" ? "600" : "400"};">
                群聊 (${groupChats.length})
              </button>
            </div>
            <div class="search-wrap">
              <input type="search" class="input input-sm" id="contactsSearchInput" placeholder="搜索${activeTab === "contacts" ? "联系人" : "群聊"}…" value="${escapeAttr(searchQuery)}" />
            </div>
          </div>
          <div class="chat-list" id="contactsListRoot" style="flex: 1; min-height: 0; overflow-y: auto;">
            ${leftListHtml}
          </div>
        </div>

        <!-- Right Pane: Detail -->
        <div class="split-main" style="display: flex; flex-direction: column; overflow: hidden; background: var(--bg-secondary);">
          <div class="split-back" style="padding: 8px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0;">
            <button class="btn btn-secondary btn-sm" id="contactsBackBtn">
              ${icon("chevronLeft", { size: "sm" })}
              <span>返回列表</span>
            </button>
          </div>
          <div style="flex: 1; min-height: 0; display: flex; overflow-y: auto;">
            ${rightDetailHtml}
          </div>
        </div>
      </div>
    </div>
  `;

  // Wire Account Switcher
  const switcher = container.querySelector("#contactsAccountSwitcher");
  if (switcher) {
    switcher.onchange = async () => {
      state.activeAccountId = switcher.value;
      await renderContactsView(container, reloadData);
    };
  }

  // Wire Refresh
  const refreshBtn = container.querySelector("#contactsRefreshBtn");
  if (refreshBtn) {
    refreshBtn.onclick = async () => {
      if (activeTab === "contacts") {
        await loadContacts(state.activeAccountId, { reset: true });
      } else if (selectedChatId) {
        await loadGroupMembers(state.activeAccountId, selectedChatId, { reset: true });
      }
      await renderContactsView(container, reloadData);
    };
  }

  // Wire Tabs
  const tabContactsBtn = container.querySelector("#tabContactsBtn");
  if (tabContactsBtn) {
    tabContactsBtn.onclick = async () => {
      if (activeTab !== "contacts") {
        activeTab = "contacts";
        searchQuery = "";
        mobilePane = "list";
        await loadContacts(state.activeAccountId, { reset: true });
        await renderContactsView(container, reloadData);
      }
    };
  }

  const tabGroupsBtn = container.querySelector("#tabGroupsBtn");
  if (tabGroupsBtn) {
    tabGroupsBtn.onclick = async () => {
      if (activeTab !== "groups") {
        activeTab = "groups";
        searchQuery = "";
        mobilePane = "list";
        if (groupChats.length > 0) {
          selectedChatId = groupChats[0].chat_id;
          await loadGroupMembers(state.activeAccountId, selectedChatId, { reset: true });
        }
        await renderContactsView(container, reloadData);
      }
    };
  }

  // Wire Search
  const searchInput = container.querySelector("#contactsSearchInput");
  if (searchInput) {
    let debounceTimer = null;
    searchInput.oninput = () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(async () => {
        searchQuery = searchInput.value.trim();
        if (activeTab === "contacts") {
          await loadContacts(state.activeAccountId, { reset: true });
        }
        await renderContactsView(container, reloadData);
      }, 300);
    };
  }

  // Wire Contact item click
  container.querySelectorAll("[data-contact-id]").forEach((btn) => {
    btn.onclick = async () => {
      selectedMemberId = btn.dataset.contactId;
      mobilePane = "detail";
      await renderContactsView(container, reloadData);
    };
  });

  // Wire Group item click
  container.querySelectorAll("[data-group-id]").forEach((btn) => {
    btn.onclick = async () => {
      selectedChatId = btn.dataset.groupId;
      mobilePane = "detail";
      memberSearchQuery = "";
      await loadGroupMembers(state.activeAccountId, selectedChatId, { reset: true });
      await renderContactsView(container, reloadData);
    };
  });

  // Wire Load More Contacts
  const loadMoreContactsBtn = container.querySelector("#loadMoreContactsBtn");
  if (loadMoreContactsBtn) {
    loadMoreContactsBtn.onclick = async () => {
      await loadContacts(state.activeAccountId, { append: true });
      await renderContactsView(container, reloadData);
    };
  }

  // Wire Member Search
  const memberSearchInput = container.querySelector("#memberSearchInput");
  if (memberSearchInput) {
    let debounceTimer = null;
    memberSearchInput.oninput = () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(async () => {
        memberSearchQuery = memberSearchInput.value.trim();
        await loadGroupMembers(state.activeAccountId, selectedChatId, { reset: true });
        await renderContactsView(container, reloadData);
      }, 300);
    };
  }

  // Wire Load More Members
  const loadMoreMembersBtn = container.querySelector("#loadMoreMembersBtn");
  if (loadMoreMembersBtn) {
    loadMoreMembersBtn.onclick = async () => {
      await loadGroupMembers(state.activeAccountId, selectedChatId, { append: true });
      await renderContactsView(container, reloadData);
    };
  }

  // Wire Back Button (mobile)
  const backBtn = container.querySelector("#contactsBackBtn");
  if (backBtn) {
    backBtn.onclick = async () => {
      mobilePane = "list";
      await renderContactsView(container, reloadData);
    };
  }

  // Wire Jump to Chat
  const gotoChatBtn = container.querySelector("#gotoChatBtn");
  if (gotoChatBtn && selectedMemberId) {
    gotoChatBtn.onclick = () => {
      state.selectedChatId = selectedMemberId;
      navigate("messages");
    };
  }

  const gotoGroupChatBtn = container.querySelector("#gotoGroupChatBtn");
  if (gotoGroupChatBtn && selectedChatId) {
    gotoGroupChatBtn.onclick = () => {
      state.selectedChatId = selectedChatId;
      navigate("messages");
    };
  }
}
