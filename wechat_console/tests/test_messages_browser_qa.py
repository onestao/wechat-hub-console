from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE_ROOT))

from wechat_console.tests import mock_core
from wechat_console.app import ConsoleService, create_server

def build_mock_state():
    state = mock_core.MockCoreState()
    state.accounts = [
        {
            "account_id": "account-qa",
            "display_name": "QA WeChat",
            "state": "online",
            "runtime": {
                "display": ":1",
                "pid": 9999,
                "healthy": True,
                "runtime_provider": "agent_wechat",
                "sender_capabilities": {
                    "text": True,
                    "image": True,
                    "file": True,
                    "driver": "agent_wechat",
                },
            },
            "sync": {"healthy": True, "last_event_at": "2026-09-02T10:00:00Z"},
        }
    ]
    state.chats = {
        "account-qa": [
            {
                "account_id": "account-qa",
                "chat_id": "qa-group@chatroom",
                "type": "group",
                "display_name": "研发群",
                "member_count": 5,
                "updated_at": "2026-09-02T10:05:00Z",
            },
            {
                "account_id": "account-qa",
                "chat_id": "chat-a",
                "type": "private",
                "display_name": "Chat A",
                "updated_at": "2026-09-02T11:00:00Z",
            },
            {
                "account_id": "account-qa",
                "chat_id": "chat-b",
                "type": "private",
                "display_name": "Chat B",
                "updated_at": "2026-09-02T12:00:00Z",
            },
            {
                "account_id": "account-qa",
                "chat_id": "chat-scroll",
                "type": "private",
                "display_name": "Scroll 300",
                "updated_at": "2026-09-02T15:00:00Z",
            },
        ]
    }
    return state

def populate_store_events(service: ConsoleService):
    events = []
    # 1. Group Chat
    events.append({
        "event_id": "grp-evt-1", "cursor": "1001", "account_id": "account-qa",
        "event_type": "message.created", "occurred_at": "2026-09-02T10:01:00Z",
        "payload": {"message": {
            "account_id": "account-qa", "message_id": "msg-grp-1", "chat_id": "qa-group@chatroom",
            "type": "text", "direction": "incoming", "created_at": "2026-09-02T10:01:00Z",
            "text": "张三发言：研发计划正式启动",
            "author": {"member_id": "zhangsan", "display_name": "张三"},
        }},
    })
    events.append({
        "event_id": "grp-evt-2", "cursor": "1002", "account_id": "account-qa",
        "event_type": "message.created", "occurred_at": "2026-09-02T10:02:00Z",
        "payload": {"message": {
            "account_id": "account-qa", "message_id": "msg-grp-2", "chat_id": "qa-group@chatroom",
            "type": "text", "direction": "incoming", "created_at": "2026-09-02T10:02:00Z",
            "text": "李四发言：架构模块设计完成",
            "author": {"member_id": "lisi", "display_name": "李四"},
        }},
    })
    events.append({
        "event_id": "grp-evt-3", "cursor": "1003", "account_id": "account-qa",
        "event_type": "message.created", "occurred_at": "2026-09-02T10:03:00Z",
        "payload": {"message": {
            "account_id": "account-qa", "message_id": "msg-grp-3", "chat_id": "qa-group@chatroom",
            "type": "text", "direction": "outgoing", "created_at": "2026-09-02T10:03:00Z",
            "text": "我发言：收到，我来推进实施",
            "author": {"member_id": "me", "is_self": True},
        }},
    })
    events.append({
        "event_id": "grp-evt-4", "cursor": "1004", "account_id": "account-qa",
        "event_type": "message.created", "occurred_at": "2026-09-02T10:04:00Z",
        "payload": {"message": {
            "account_id": "account-qa", "message_id": "msg-grp-4", "chat_id": "qa-group@chatroom",
            "type": "text", "direction": "incoming", "created_at": "2026-09-02T10:04:00Z",
            "text": "王五发言：无显示名称群成员测试",
            "author": {"member_id": "wangwu"},
        }},
    })

    # 2. Chat A: 150 messages
    for i in range(1, 151):
        ts = f"2026-09-02T11:{i//60:02d}:{i%60:02d}Z"
        events.append({
            "event_id": f"a-evt-{i}", "cursor": str(2000 + i), "account_id": "account-qa",
            "event_type": "message.created", "occurred_at": ts,
            "payload": {"message": {
                "account_id": "account-qa", "message_id": f"chat-a-msg-{i}", "chat_id": "chat-a",
                "type": "text", "direction": "incoming", "created_at": ts,
                "text": f"Chat A exclusive record #{i}",
                "author": {"member_id": "alice", "display_name": "Alice"},
            }},
        })

    # 3. Chat B: 150 messages
    for i in range(1, 151):
        ts = f"2026-09-02T12:{i//60:02d}:{i%60:02d}Z"
        events.append({
            "event_id": f"b-evt-{i}", "cursor": str(3000 + i), "account_id": "account-qa",
            "event_type": "message.created", "occurred_at": ts,
            "payload": {"message": {
                "account_id": "account-qa", "message_id": f"chat-b-msg-{i}", "chat_id": "chat-b",
                "type": "text", "direction": "incoming", "created_at": ts,
                "text": f"Chat B exclusive record #{i}",
                "author": {"member_id": "bob", "display_name": "Bob"},
            }},
        })

    # 4. Scroll 300: 300 messages
    for i in range(1, 301):
        minute = i % 60
        hour = 13 + (i // 60)
        ts = f"2026-09-02T{hour:02d}:{minute:02d}:00Z"
        events.append({
            "event_id": f"scroll-evt-{i}", "cursor": str(4000 + i), "account_id": "account-qa",
            "event_type": "message.created", "occurred_at": ts,
            "payload": {"message": {
                "account_id": "account-qa", "message_id": f"scroll-msg-{i}", "chat_id": "chat-scroll",
                "type": "text", "direction": "incoming" if i % 2 == 1 else "outgoing", "created_at": ts,
                "text": f"Scroll test item #{i} [created at {ts}]",
                "author": {"member_id": "tester", "display_name": "Tester"} if i % 2 == 1 else {"member_id": "me", "is_self": True},
            }},
        })

    service.store.ingest_events(events, "9999")


def run_qa():
    mock_state = build_mock_state()
    mock_server = mock_core.create_server("127.0.0.1", 0, mock_state)
    mock_port = mock_server.server_port
    mock_thread = threading.Thread(target=mock_server.serve_forever, daemon=True)
    mock_thread.start()

    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    service = ConsoleService(
        core_url=f"http://127.0.0.1:{mock_port}",
        db_path=root / "console.sqlite",
        archive_dir=root / "saved",
    )
    populate_store_events(service)

    console_server = create_server("127.0.0.1", 0, service)
    console_port = console_server.server_port
    console_thread = threading.Thread(target=console_server.serve_forever, daemon=True)
    console_thread.start()

    base_url = f"http://127.0.0.1:{console_port}"
    print(f"Mock Core on {mock_port}, Console Server on {console_port}")

    errors = []
    results = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            page.on("pageerror", lambda err: errors.append(f"PageError: {err}"))
            page.on("console", lambda msg: errors.append(f"ConsoleError: {msg.text}") if msg.type == "error" and "favicon" not in msg.text and "404" not in msg.text else None)
            page.on("response", lambda res: print(f"HTTP {res.status}: {res.url}") if res.status >= 400 else None)

            # Navigate to Messages page
            page.goto(f"{base_url}/#messages", wait_until="networkidle")
            page.wait_for_selector(".page[data-route='messages']:not([hidden])", timeout=5000)

            # ------------------------------------------------------------------
            # Test Suite 1: D11 Group chat display & author naming
            # ------------------------------------------------------------------
            print("\n--- Test Suite 1: Group Chat Contract & Author Naming ---")
            chat_items = page.locator(".chat-item")
            chat_names = chat_items.all_inner_texts()
            print(f"Chat list items found: {chat_items.count()}")
            
            # Assert "研发群" is in chat list, and no chat displays raw "qa-group@chatroom"
            grp_item = page.locator(".chat-item[data-chat-id='qa-group@chatroom']")
            expect(grp_item).to_be_visible()
            grp_name = grp_item.locator(".chat-item-name").inner_text()
            assert grp_name == "研发群", f"Expected '研发群', got '{grp_name}'"
            assert "qa-group@chatroom" not in grp_name

            # Click group chat
            grp_item.click()
            page.wait_for_timeout(500)

            # Header assertions
            toolbar_title = page.locator(".chat-toolbar-title .item-title").inner_text()
            assert "研发群" in toolbar_title, f"Header should have '研发群', got {toolbar_title}"
            assert "群聊" in toolbar_title, f"Header should have '群聊' badge, got {toolbar_title}"

            # Check message senders
            thread_text = page.locator("#messagesThreadRoot").inner_text()
            assert "张三" in thread_text, "Should display sender '张三'"
            assert "李四" in thread_text, "Should display sender '李四'"
            assert "我" in thread_text, "Should display sender '我'"
            assert "对方" not in thread_text, "Group chat MUST NOT fallback to '对方'"
            print("PASS: Suite 1 (研发群 group marker, senders 张三/李四/我, zero '对方' fallback)")
            results["group_contract"] = "PASS"

            # ------------------------------------------------------------------
            # Test Suite 2: D11 Scoped loading isolation (Chat A vs Chat B)
            # ------------------------------------------------------------------
            print("\n--- Test Suite 2: Scoped Loading Isolation (Chat A vs Chat B) ---")
            scoped_requests = []
            def on_request(req):
                if "/api/messages" in req.url:
                    scoped_requests.append(req.url)
            page.on("request", on_request)

            # Switch to Chat A
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_timeout(600)
            a_msgs = page.locator("#messagesThreadRoot .bubble-text").all_inner_texts()
            assert any("Chat A" in m for m in a_msgs), "Chat A messages must render"
            assert not any("Chat B" in m for m in a_msgs), "Chat A must NOT contain Chat B messages"
            assert any("chat_id=chat-a" in url for url in scoped_requests), "Scoped request for chat-a must be fired"

            # Switch to Chat B
            scoped_requests.clear()
            page.locator(".chat-item[data-chat-id='chat-b']").click()
            page.wait_for_timeout(600)
            b_msgs = page.locator("#messagesThreadRoot .bubble-text").all_inner_texts()
            assert any("Chat B" in m for m in b_msgs), "Chat B messages must render"
            assert not any("Chat A" in m for m in b_msgs), "Chat B must NOT contain Chat A messages"
            assert any("chat_id=chat-b" in url for url in scoped_requests), "Scoped request for chat-b must be fired"
            print("PASS: Suite 2 (Scoped loading isolation between Chat A and Chat B verified)")
            results["scoped_isolation"] = "PASS"

            # ------------------------------------------------------------------
            # Test Suite 3: D11 300 messages scroll, visual anchor & auto-scroll rules
            # ------------------------------------------------------------------
            print("\n--- Test Suite 3: 300 Messages Scroll & Visual Anchoring ---")
            page.locator(".chat-item[data-chat-id='chat-scroll']").click()
            page.wait_for_timeout(600)

            thread = page.locator("#messagesThreadRoot")
            metrics = thread.evaluate("el => ({ scrollHeight: el.scrollHeight, clientHeight: el.clientHeight, scrollTop: el.scrollTop })")
            print(f"Scroll metrics: {metrics}")
            assert metrics["scrollHeight"] > metrics["clientHeight"], "scrollHeight must exceed clientHeight"

            # Composer and Toolbar visible
            expect(page.locator(".chat-toolbar")).to_be_visible()
            expect(page.locator(".composer")).to_be_visible()

            # Latest message at bottom
            last_bubble = page.locator("#messagesThreadRoot .bubble-line").last
            last_text = last_bubble.inner_text()
            assert "Scroll test item #300" in last_text, f"Latest message #300 must be at bottom, got {last_text}"

            # Load older button exists
            load_older_btn = page.locator("#messagesLoadOlderBtn")
            expect(load_older_btn).to_be_visible()

            # Scroll up towards top
            thread.evaluate("el => { el.scrollTop = 150; }")
            page.wait_for_timeout(200)
            # Dispatch click directly so Playwright's click action doesn't auto-scroll to element
            top_before = thread.evaluate("el => el.scrollTop")
            height_before = thread.evaluate("el => el.scrollHeight")
            load_older_btn.evaluate("el => el.click()")
            page.wait_for_timeout(800)

            top_after = thread.evaluate("el => el.scrollTop")
            height_after = thread.evaluate("el => el.scrollHeight")
            print(f"Prepend anchor: before (top={top_before}, h={height_before}) -> after (top={top_after}, h={height_after})")
            expected_top = top_before + (height_after - height_before)
            assert abs(top_after - expected_top) <= 5, f"Visual anchor failed: expected ~{expected_top}, got {top_after}"

            # Test background rerender does not jump to bottom when user is reading history
            thread.evaluate("el => { el.scrollTop = 300; }")
            page.wait_for_timeout(200)
            history_scroll_top = thread.evaluate("el => el.scrollTop")

            # Trigger background rerender via type filter or state refresh
            page.locator("#messagesChatSearchInput").fill("S")
            page.wait_for_timeout(200)
            page.locator("#messagesChatSearchInput").fill("")
            page.wait_for_timeout(200)
            after_rerender_top = thread.evaluate("el => el.scrollTop")
            assert abs(after_rerender_top - history_scroll_top) <= 5, f"Rerender jumped scroll! was {history_scroll_top}, now {after_rerender_top}"
            print("PASS: Suite 3 (300 msgs scrollHeight>clientHeight, visual anchoring preserved, background rerender stable)")
            results["scroll_and_anchoring"] = "PASS"

            # ------------------------------------------------------------------
            # Test Suite 4: D12 Responsive Viewports
            # ------------------------------------------------------------------
            print("\n--- Test Suite 4: Responsive Breakpoints ---")
            viewports = [
                (1440, 900, "desktop_wide"),
                (1024, 768, "desktop_standard"),
                (768, 1024, "tablet"),
                (390, 844, "mobile_phone"),
            ]
            for w, h, name in viewports:
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(300)
                expect(page.locator("#messagesThreadRoot")).to_be_visible()
                expect(page.locator(".composer")).to_be_visible()
                print(f"  Viewport {w}x{h} ({name}): OK")

            # On mobile (390x844), test mobile back button navigation
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)

            back_btn = page.locator("#messagesMobileBackBtn")
            expect(back_btn).to_be_visible()
            back_btn.click()
            page.wait_for_timeout(300)

            # In list pane, chat list is visible, split-main is hidden
            expect(page.locator(".split-side")).to_be_visible()
            expect(page.locator(".split-main")).not_to_be_visible()

            # Click a chat to go to detail
            page.locator(".chat-item[data-chat-id='qa-group@chatroom']").click()
            page.wait_for_timeout(300)
            expect(page.locator(".split-main")).to_be_visible()
            expect(page.locator(".split-side")).not_to_be_visible()
            print("PASS: Suite 4 (Responsive viewports 1440x900, 1024x768, 768x1024, 390x844 & mobile pane toggling)")
            results["responsive_mobile"] = "PASS"

            # ------------------------------------------------------------------
            # Test Suite 5: D13 Regressions (Send text, Retry, Save modal)
            # ------------------------------------------------------------------
            print("\n--- Test Suite 5: Regressions & Zero Unhandled JS Errors ---")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(300)

            # Send a text message
            textarea = page.locator("#composerTextarea")
            send_btn = page.locator("#composerSendBtn")
            textarea.fill("Playwright test automated message")
            send_btn.click()
            page.wait_for_timeout(600)

            # Verify send status banner appears
            send_banner = page.locator(".send-result")
            expect(send_banner).to_be_visible()
            banner_text = send_banner.inner_text()
            print(f"Send status banner text: {banner_text}")
            assert any(term in banner_text for term in ["发送中", "提交", "已确认", "排队"]), f"Unexpected banner: {banner_text}"

            # Verify Save Message modal
            save_btn = page.locator(".bubble-save-btn").first
            save_btn.click()
            page.wait_for_timeout(400)
            save_dialog = page.locator("#saveMessageModal")
            expect(save_dialog).to_be_visible()
            page.locator("#saveDialogCloseBtn").click()
            page.wait_for_timeout(300)
            expect(save_dialog).not_to_be_visible()

            # Verify zero JS errors
            print(f"\nCollected JS Errors during whole session: {errors}")
            assert len(errors) == 0, f"Detected unhandled JS errors: {errors}"
            print("PASS: Suite 5 (Send lifecycle, save modal, zero unhandled errors)")
            results["regressions_and_clean_console"] = "PASS"

            browser.close()

    finally:
        console_server.shutdown()
        console_server.server_close()
        mock_server.shutdown()
        mock_server.server_close()
        temp_dir.cleanup()

    print("\n==========================================")
    print("ALL BROWSER QA TEST SUITES PASSED:")
    for k, v in results.items():
        print(f"  - {k}: {v}")
    print("==========================================")


import unittest

class MessagesBrowserQATest(unittest.TestCase):
    def test_browser_messages_v2_e2e(self):
        run_qa()

if __name__ == "__main__":
    unittest.main()
