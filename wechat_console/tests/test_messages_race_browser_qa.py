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


def build_race_mock_state():
    state = mock_core.MockCoreState()
    state.accounts = [
        {
            "account_id": "account-a",
            "display_name": "Account A",
            "state": "online",
            "instance_uuid": "instance-A",
            "wechat_identity_uuid": "identity-A",
            "runtime": {
                "display": ":1",
                "pid": 9001,
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
        },
        {
            "account_id": "account-b",
            "display_name": "Account B",
            "state": "online",
            "instance_uuid": "instance-B",
            "wechat_identity_uuid": "identity-B",
            "runtime": {
                "display": ":2",
                "pid": 9002,
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
        },
    ]
    state.chats = {
        "account-a": [
            {
                "account_id": "account-a",
                "chat_id": "chat-a",
                "type": "private",
                "display_name": "Chat A",
                "updated_at": "2026-09-02T11:00:00Z",
            },
            {
                "account_id": "account-a",
                "chat_id": "shared-contact",
                "type": "private",
                "display_name": "Shared Contact",
                "updated_at": "2026-09-02T11:30:00Z",
            },
        ],
        "account-b": [
            {
                "account_id": "account-b",
                "chat_id": "chat-b",
                "type": "private",
                "display_name": "Chat B",
                "updated_at": "2026-09-02T12:00:00Z",
            },
            {
                "account_id": "account-b",
                "chat_id": "shared-contact",
                "type": "private",
                "display_name": "Shared Contact",
                "updated_at": "2026-09-02T12:30:00Z",
            },
        ],
    }
    return state


def populate_race_events(service: ConsoleService):
    events = []
    # 1. account-a / chat-a: 150 messages (for pagination tests)
    for i in range(1, 151):
        ts = f"2026-09-02T11:{i//60:02d}:{i%60:02d}Z"
        events.append({
            "event_id": f"evt-a-{i}",
            "cursor": str(1000 + i),
            "account_id": "account-a",
            "event_type": "message.created",
            "occurred_at": ts,
            "payload": {
                "message": {
                    "account_id": "account-a",
                    "instance_uuid": "instance-A",
                    "wechat_identity_uuid": "identity-A",
                    "message_id": f"msg-a-{i}",
                    "chat_id": "chat-a",
                    "type": "text",
                    "direction": "incoming",
                    "created_at": ts,
                    "text": f"Chat A record #{i}",
                    "author": {"member_id": "alice", "display_name": "Alice"},
                }
            },
        })

    # 2. account-a / shared-contact: 10 messages
    for i in range(1, 11):
        ts = f"2026-09-02T11:30:{i:02d}Z"
        events.append({
            "event_id": f"evt-a-shared-{i}",
            "cursor": str(2000 + i),
            "account_id": "account-a",
            "event_type": "message.created",
            "occurred_at": ts,
            "payload": {
                "message": {
                    "account_id": "account-a",
                    "instance_uuid": "instance-A",
                    "wechat_identity_uuid": "identity-A",
                    "message_id": f"msg-a-shared-{i}",
                    "chat_id": "shared-contact",
                    "type": "text",
                    "direction": "incoming",
                    "created_at": ts,
                    "text": f"[Account A] Shared Contact Msg #{i}",
                    "author": {"member_id": "shared_user", "display_name": "Shared User"},
                }
            },
        })

    # 3. account-b / chat-b: 20 messages
    for i in range(1, 21):
        ts = f"2026-09-02T12:{i//60:02d}:{i%60:02d}Z"
        events.append({
            "event_id": f"evt-b-{i}",
            "cursor": str(3000 + i),
            "account_id": "account-b",
            "event_type": "message.created",
            "occurred_at": ts,
            "payload": {
                "message": {
                    "account_id": "account-b",
                    "instance_uuid": "instance-B",
                    "wechat_identity_uuid": "identity-B",
                    "message_id": f"msg-b-{i}",
                    "chat_id": "chat-b",
                    "type": "text",
                    "direction": "incoming",
                    "created_at": ts,
                    "text": f"[Account B] Chat B record #{i}",
                    "author": {"member_id": "bob", "display_name": "Bob"},
                }
            },
        })

    # 4. account-b / shared-contact: 10 messages
    for i in range(1, 11):
        ts = f"2026-09-02T12:30:{i:02d}Z"
        events.append({
            "event_id": f"evt-b-shared-{i}",
            "cursor": str(4000 + i),
            "account_id": "account-b",
            "event_type": "message.created",
            "occurred_at": ts,
            "payload": {
                "message": {
                    "account_id": "account-b",
                    "instance_uuid": "instance-B",
                    "wechat_identity_uuid": "identity-B",
                    "message_id": f"msg-b-shared-{i}",
                    "chat_id": "shared-contact",
                    "type": "text",
                    "direction": "incoming",
                    "created_at": ts,
                    "text": f"[Account B] Shared Contact Msg #{i}",
                    "author": {"member_id": "shared_user", "display_name": "Shared User"},
                }
            },
        })

    service.store.ingest_events(events, "9999")


def run_race_qa():
    mock_state = build_race_mock_state()
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
    populate_race_events(service)

    # Controllable barrier registries for test determinism
    list_barriers = {}
    list_entered = {}
    orig_list_messages = service.store.list_messages

    def hooked_list_messages(*args, **kwargs):
        account_id = kwargs.get("account_id") or (args[0] if len(args) > 0 else "")
        chat_id = kwargs.get("chat_id") or (args[3] if len(args) > 3 else "")
        before = kwargs.get("before") or (args[7] if len(args) > 7 else "")
        key = f"{account_id}:{chat_id}"
        if before:
            key += ":pagination"

        if key in list_entered:
            list_entered[key].set()
        if key in list_barriers:
            list_barriers[key].wait(timeout=10.0)

        return orig_list_messages(*args, **kwargs)

    service.store.list_messages = hooked_list_messages

    # Controllable send status barriers
    status_barriers = {}
    status_entered = {}
    orig_get_send = service.store.get_send

    def hooked_get_send(send_id, **kwargs):
        if send_id in status_entered:
            status_entered[send_id].set()
        if send_id in status_barriers:
            status_barriers[send_id].wait(timeout=10.0)
        res = orig_get_send(send_id, **kwargs)
        if res:
            res = dict(res)
            res["status"] = "sent"
            res["delivery_certainty"] = "confirmed"
        return res

    service.store.get_send = hooked_get_send

    # Controllable send text barriers
    send_text_barriers = {}
    send_text_entered = {}
    orig_send_text = service.core.send_text

    def hooked_send_text(*args, **kwargs):
        key = "send_text"
        if key in send_text_entered:
            send_text_entered[key].set()
        if key in send_text_barriers:
            send_text_barriers[key].wait(timeout=10.0)
        return orig_send_text(*args, **kwargs)

    service.core.send_text = hooked_send_text

    # Media send tracker for P0 safety checks
    media_send_calls = []
    orig_send_media = service.core.send_media

    def hooked_send_media(*args, **kwargs):
        media_send_calls.append({"args": args, "kwargs": kwargs})
        return orig_send_media(*args, **kwargs)

    service.core.send_media = hooked_send_media

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
            page.on(
                "console",
                lambda msg: errors.append(f"ConsoleError: {msg.text}")
                if msg.type == "error" and "favicon" not in msg.text and "404" not in msg.text
                else None,
            )

            # Navigate to Messages page
            page.goto(f"{base_url}/#messages", wait_until="networkidle")
            page.wait_for_selector(".page[data-route='messages']:not([hidden])", timeout=5000)

            # ------------------------------------------------------------------
            # Race B1: Old chat fetch returns last
            # ------------------------------------------------------------------
            print("\n--- Race B1: Old chat fetch returns last ---")
            # Step 1: Ensure we switch to Shared Contact first so Chat A is not currently selected
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account A] Shared Contact Msg #1')"
            )

            # Step 2: Now setup barrier for Chat A
            barrier_key = "account-a:chat-a"
            list_entered[barrier_key] = threading.Event()
            list_barriers[barrier_key] = threading.Event()

            # Step 3: Click Chat A to initiate fetch
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            assert list_entered[barrier_key].wait(timeout=5.0), "Request for Chat A did not start"

            # Step 4: Now switch back to Shared Contact while Chat A is still held
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account A] Shared Contact Msg #1')"
            )

            # Step 5: Release Chat A response
            list_barriers[barrier_key].set()
            page.wait_for_timeout(300)

            # Assert view is still Shared Contact and contains ZERO Chat A bubbles
            toolbar_title = page.locator(".chat-toolbar-title .item-title").inner_text()
            assert "Shared Contact" in toolbar_title, f"Expected Shared Contact, got {toolbar_title}"
            thread_text = page.locator("#messagesThreadRoot").inner_text()
            assert "Chat A record" not in thread_text, "Stale Chat A messages leaked into Shared Contact!"
            assert "[Account A] Shared Contact" in thread_text
            print("PASS: Race B1 (old chat fetch returned last; zero leakage into newly selected chat)")
            results["Race_B1"] = "PASS"

            # Cleanup B1 barriers
            del list_barriers[barrier_key]
            del list_entered[barrier_key]

            # ------------------------------------------------------------------
            # Race B2: Rapid account switch (same chat_id 'shared-contact' across accounts)
            # ------------------------------------------------------------------
            print("\n--- Race B2: Rapid account switch with shared chat_id ---")
            # Currently on account-a / shared-contact.
            # Switch to chat-a first so clicking shared-contact will trigger a fetch
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector("#messagesThreadRoot .bubble-line:has-text('Chat A record #1')")

            barrier_key_a = "account-a:shared-contact"
            list_entered[barrier_key_a] = threading.Event()
            list_barriers[barrier_key_a] = threading.Event()

            # Click shared-contact to start request
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            assert list_entered[barrier_key_a].wait(timeout=5.0), "Request for account-a:shared-contact did not start"

            # Immediately switch account to account-b via switcher
            switcher = page.locator("#messagesAccountSwitcher")
            switcher.select_option("account-b")

            # Wait for account-b chats to render, then click shared-contact on account-b
            page.wait_for_selector(".chat-item[data-chat-id='shared-contact']")
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account B] Shared Contact Msg #1')"
            )

            # Release account-a shared-contact
            list_barriers[barrier_key_a].set()
            page.wait_for_timeout(300)

            thread_text = page.locator("#messagesThreadRoot").inner_text()
            assert "[Account B] Shared Contact" in thread_text
            assert "[Account A]" not in thread_text, "Stale Account A data leaked to Account B thread!"
            print("PASS: Race B2 (rapid account switch; cross-account identity isolation preserved)")
            results["Race_B2"] = "PASS"

            del list_barriers[barrier_key_a]
            del list_entered[barrier_key_a]

            # ------------------------------------------------------------------
            # Race B3: Pagination response after chat switch
            # ------------------------------------------------------------------
            print("\n--- Race B3: Pagination response after chat switch ---")
            switcher.select_option("account-a")
            page.wait_for_selector(".chat-item[data-chat-id='chat-a']")
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector("#messagesLoadOlderBtn")

            pag_key = "account-a:chat-a:pagination"
            list_entered[pag_key] = threading.Event()
            list_barriers[pag_key] = threading.Event()

            load_older_btn = page.locator("#messagesLoadOlderBtn")
            load_older_btn.click()
            assert list_entered[pag_key].wait(timeout=5.0), "Pagination request did not start"

            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account A] Shared Contact Msg #1')"
            )

            list_barriers[pag_key].set()
            page.wait_for_timeout(300)

            thread_text = page.locator("#messagesThreadRoot").inner_text()
            assert "Chat A record" not in thread_text, "Older Chat A page prepended to Shared Contact!"
            assert "[Account A] Shared Contact" in thread_text
            print("PASS: Race B3 (pagination response arrived after chat switch; ignored cleanly)")
            results["Race_B3"] = "PASS"

            del list_barriers[pag_key]
            del list_entered[pag_key]

            # ------------------------------------------------------------------
            # Race B4: Old full refresh vs newer full refresh for same chat
            # ------------------------------------------------------------------
            print("\n--- Race B4: Old full refresh vs newer full refresh same chat ---")
            # Currently on shared-contact. Switch to chat-a first
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector("#messagesThreadRoot .bubble-line:has-text('Chat A record #1')")

            sc_key = "account-a:shared-contact"
            list_entered[sc_key] = threading.Event()
            list_barriers[sc_key] = threading.Event()

            # Trigger Request #1 by clicking shared-contact
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            assert list_entered[sc_key].wait(timeout=5.0), "Request #1 did not enter"

            # Ingest newer message into store while Request #1 is held
            newer_event = {
                "event_id": "evt-a-shared-newest",
                "cursor": "9999",
                "account_id": "account-a",
                "event_type": "message.created",
                "occurred_at": "2026-09-02T11:59:59Z",
                "payload": {
                    "message": {
                        "account_id": "account-a",
                        "instance_uuid": "instance-A",
                        "wechat_identity_uuid": "identity-A",
                        "message_id": "msg-a-shared-newest",
                        "chat_id": "shared-contact",
                        "type": "text",
                        "direction": "incoming",
                        "created_at": "2026-09-02T11:59:59Z",
                        "text": "EXCLUSIVE_NEWEST_MESSAGE_9999",
                        "author": {"member_id": "admin", "display_name": "Admin"},
                    }
                },
            }
            service.store.ingest_events([newer_event], "10000")

            del list_entered[sc_key]
            barrier_to_release = list_barriers.pop(sc_key)

            # Trigger Request #2 by switching to chat-a and back to shared-contact
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector("#messagesThreadRoot .bubble-line:has-text('Chat A record #1')")
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector("#messagesThreadRoot .bubble-line:has-text('EXCLUSIVE_NEWEST_MESSAGE_9999')")

            # Release Request #1
            barrier_to_release.set()
            page.wait_for_timeout(300)

            thread_text = page.locator("#messagesThreadRoot").inner_text()
            assert "EXCLUSIVE_NEWEST_MESSAGE_9999" in thread_text, "Request #1 clobbered Request #2!"
            print("PASS: Race B4 (older full refresh could not overwrite newer full refresh)")
            results["Race_B4"] = "PASS"

            # ------------------------------------------------------------------
            # Race B5: Stale finally flags do not clear loading state prematurely
            # ------------------------------------------------------------------
            print("\n--- Race B5: Stale finally flags protection ---")
            chat_a_key = "account-a:chat-a"
            list_entered[chat_a_key] = threading.Event()
            list_barriers[chat_a_key] = threading.Event()

            page.locator(".chat-item[data-chat-id='chat-a']").click()
            assert list_entered[chat_a_key].wait(timeout=5.0)

            page.locator(".chat-item[data-chat-id='shared-contact']").click()

            list_barriers[chat_a_key].set()
            page.wait_for_timeout(300)

            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector("#messagesLoadOlderBtn")
            btn = page.locator("#messagesLoadOlderBtn")
            expect(btn).to_be_enabled()
            assert "加载更早消息" in btn.inner_text()
            print("PASS: Race B5 (stale finally flags safely contained)")
            results["Race_B5"] = "PASS"

            del list_barriers[chat_a_key]
            del list_entered[chat_a_key]

            # ------------------------------------------------------------------
            # Race B6: Send status after chat switch
            # ------------------------------------------------------------------
            print("\n--- Race B6: Send status arrives after chat switch ---")
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector("#composerTextarea")

            status_held = threading.Event()
            status_release = threading.Event()

            def hold_first_status(send_id, **kw):
                status_held.set()
                status_release.wait(timeout=10.0)
                return {
                    "send_id": send_id,
                    "status": "sent",
                    "delivery_certainty": "confirmed",
                    "echo_message_id": "echo-b6",
                }

            service.store.get_send = hold_first_status

            textarea = page.locator("#composerTextarea")
            send_btn = page.locator("#composerSendBtn")
            textarea.fill("Message for Race B6")
            send_btn.click()

            assert status_held.wait(timeout=5.0), "Status polling did not start"

            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account A] Shared Contact Msg #1')"
            )

            status_release.set()
            page.wait_for_timeout(400)

            expect(page.locator(".send-result")).not_to_be_visible()
            sc_text = page.locator("#messagesThreadRoot").inner_text()
            assert "Message for Race B6" not in sc_text, "Chat A send status refreshed Shared Contact!"
            print("PASS: Race B6 (send status arrived after switch; zero side effect on new chat)")
            results["Race_B6"] = "PASS"

            service.store.get_send = hooked_get_send

            # ------------------------------------------------------------------
            # Race B7: Text send receipt/error arrives after switch
            # ------------------------------------------------------------------
            print("\n--- Race B7: Text send receipt/error arrives after switch ---")
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('Chat A record #150')"
            )

            send_text_barriers["send_text"] = threading.Event()
            send_text_entered["send_text"] = threading.Event()

            textarea = page.locator("#composerTextarea")
            send_btn = page.locator("#composerSendBtn")
            textarea.fill("Message for Race B7 late receipt")
            send_btn.click()

            assert send_text_entered["send_text"].wait(timeout=5.0), "Send text request not captured"

            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account A] Shared Contact Msg #1')"
            )

            send_text_barriers["send_text"].set()
            page.wait_for_timeout(400)

            expect(page.locator(".send-result")).not_to_be_visible()
            print("PASS: Race B7 (text send response arrived after switch; no UI leakage)")
            results["Race_B7"] = "PASS"

            del send_text_barriers["send_text"]
            del send_text_entered["send_text"]

            # ------------------------------------------------------------------
            # Race B8: Image FileReader owner change (P0 Fail-Closed Safety)
            # ------------------------------------------------------------------
            print("\n--- Race B8: Image FileReader owner change (P0 safety) ---")
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account A] Shared Contact Msg #1')"
            )
            page.wait_for_selector("#composerImageInput", state="attached")

            page.evaluate("""
                window.__releaseFileReader = null;
                window.__origReadAsDataURL = FileReader.prototype.readAsDataURL;
                FileReader.prototype.readAsDataURL = function(file) {
                    const self = this;
                    window.__releaseFileReader = () => {
                        window.__origReadAsDataURL.call(self, file);
                    };
                };
            """)

            media_send_calls.clear()

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tf.write(mock_core.SAMPLE_PNG)
                tf_png_path = tf.name

            try:
                page.set_input_files("#composerImageInput", tf_png_path)
                page.wait_for_function("() => typeof window.__releaseFileReader === 'function'")

                switcher.select_option("account-b")
                page.wait_for_selector(".chat-item[data-chat-id='shared-contact']")
                page.locator(".chat-item[data-chat-id='shared-contact']").click()
                page.wait_for_selector(
                    "#messagesThreadRoot .bubble-line:has-text('[Account B] Shared Contact Msg #1')"
                )

                page.evaluate("() => window.__releaseFileReader()")
                page.wait_for_timeout(500)

                assert len(media_send_calls) == 0, f"api.sendImage was called {len(media_send_calls)} times after owner change!"

                toast_el = page.locator(".toast").last
                expect(toast_el).to_be_visible()
                toast_text = toast_el.inner_text()
                assert "会话已切换，已取消本次附件发送" in toast_text, f"Unexpected toast: {toast_text}"
                print("PASS: Race B8 (image FileReader owner changed -> fail-closed, 0 send calls, toast shown)")
                results["Race_B8"] = "PASS"
            finally:
                if os.path.exists(tf_png_path):
                    os.unlink(tf_png_path)

            # ------------------------------------------------------------------
            # Race B9: File FileReader owner change (P0 Fail-Closed Safety)
            # ------------------------------------------------------------------
            print("\n--- Race B9: File FileReader owner change (P0 safety) ---")
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account B] Shared Contact Msg #1')"
            )
            page.wait_for_selector("#composerFileInput", state="attached")

            page.evaluate("""
                window.__releaseFileReader = null;
                FileReader.prototype.readAsDataURL = function(file) {
                    const self = this;
                    window.__releaseFileReader = () => {
                        window.__origReadAsDataURL.call(self, file);
                    };
                };
            """)

            media_send_calls.clear()

            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
                tf.write(b"Hello test file content")
                tf_txt_path = tf.name

            try:
                page.set_input_files("#composerFileInput", tf_txt_path)
                page.wait_for_function("() => typeof window.__releaseFileReader === 'function'")

                switcher.select_option("account-a")
                page.wait_for_selector(".chat-item[data-chat-id='chat-a']")
                page.locator(".chat-item[data-chat-id='chat-a']").click()
                page.wait_for_selector(
                    "#messagesThreadRoot .bubble-line:has-text('Chat A record #150')"
                )

                page.evaluate("() => window.__releaseFileReader()")
                page.wait_for_timeout(500)

                assert len(media_send_calls) == 0, f"api.sendFile was called {len(media_send_calls)} times after owner change!"

                toast_el = page.locator(".toast").last
                expect(toast_el).to_be_visible()
                toast_text = toast_el.inner_text()
                assert "会话已切换，已取消本次附件发送" in toast_text, f"Unexpected toast: {toast_text}"
                print("PASS: Race B9 (file FileReader owner changed -> fail-closed, 0 send calls, toast shown)")
                results["Race_B9"] = "PASS"
            finally:
                if os.path.exists(tf_txt_path):
                    os.unlink(tf_txt_path)

            page.evaluate("() => { FileReader.prototype.readAsDataURL = window.__origReadAsDataURL; }")

            # ------------------------------------------------------------------
            # Race B10: Current-owner normal path (all functions remain fully working)
            # ------------------------------------------------------------------
            print("\n--- Race B10: Current-owner normal path ---")
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector("#messagesThreadRoot .bubble-line:has-text('Chat A record #150')")

            thread_el = page.locator("#messagesThreadRoot")
            h_before = thread_el.evaluate("el => el.scrollHeight")
            load_older = page.locator("#messagesLoadOlderBtn")
            load_older.click()
            page.wait_for_timeout(600)
            h_after = thread_el.evaluate("el => el.scrollHeight")
            assert h_after > h_before, f"Expected height increase after pagination: before={h_before}, after={h_after}"

            textarea = page.locator("#composerTextarea")
            send_btn = page.locator("#composerSendBtn")
            textarea.fill("Normal message for current owner B10")
            send_btn.click()
            page.wait_for_selector(".send-result", timeout=3000)
            banner_text = page.locator(".send-result").inner_text()
            assert any(k in banner_text for k in ["发送中", "排队", "提交", "已确认"])
            page.wait_for_selector(".send-result[data-state='sent']", timeout=5000)

            # Simulate failure retry
            page.evaluate("""
                import('./js/state.js').then(({ setState }) => {
                    import('./js/views/messages.js').then(({ captureMessageOwnerContext }) => {
                        const ctx = captureMessageOwnerContext();
                        setState({
                            sendResult: {
                                owner_key: ctx.owner_key,
                                generation: ctx.generation,
                                status: "failed",
                                error: "Simulated test failure",
                                kind: "text",
                                text: "Text to retry",
                            }
                        });
                        const container = document.querySelector("#messagesThreadRoot").closest(".page");
                        const reloadData = () => Promise.resolve();
                        import('./js/views/messages.js').then(({ renderMessagesView }) => {
                            renderMessagesView(container, reloadData);
                        });
                    });
                });
            """)
            page.wait_for_selector("#msgRetrySendBtn")
            retry_btn = page.locator("#msgRetrySendBtn")
            retry_btn.click()
            page.wait_for_timeout(300)
            assert textarea.input_value() == "Text to retry", "Retry failed to populate textarea"

            # Uncertain dismiss
            page.evaluate("""
                import('./js/state.js').then(({ setState }) => {
                    import('./js/views/messages.js').then(({ captureMessageOwnerContext }) => {
                        const ctx = captureMessageOwnerContext();
                        setState({
                            sendResult: {
                                owner_key: ctx.owner_key,
                                generation: ctx.generation,
                                status: "uncertain",
                                delivery_certainty: "uncertain",
                                kind: "text",
                                text: "Uncertain text",
                            }
                        });
                        const container = document.querySelector("#messagesThreadRoot").closest(".page");
                        const reloadData = () => Promise.resolve();
                        import('./js/views/messages.js').then(({ renderMessagesView }) => {
                            renderMessagesView(container, reloadData);
                        });
                    });
                });
            """)
            page.wait_for_selector("#msgDismissUncertainBtn")
            page.locator("#msgDismissUncertainBtn").click()
            page.wait_for_timeout(300)
            expect(page.locator(".send-result")).not_to_be_visible()

            print("PASS: Race B10 (all normal flows functional on current owner)")
            results["Race_B10"] = "PASS"

            print(f"\nFinal collected JS Errors: {errors}")
            assert len(errors) == 0, f"Unhandled JS errors during session: {errors}"

            browser.close()
    finally:
        console_server.shutdown()
        console_server.server_close()
        mock_server.shutdown()
        mock_server.server_close()
        temp_dir.cleanup()

    print("\n==========================================")
    print("ALL DETERMINISTIC RACE TEST SUITES PASSED:")
    for k, v in results.items():
        print(f"  - {k}: {v}")
    print("==========================================")


import unittest


class MessagesDeterministicRaceBrowserTest(unittest.TestCase):
    def test_messages_deterministic_races_b1_to_b10(self):
        run_race_qa()


if __name__ == "__main__":
    unittest.main()
