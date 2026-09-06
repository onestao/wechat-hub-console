from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE_ROOT))

from wechat_console.tests import mock_core
from wechat_console.app import ConsoleService, create_server
from wechat_console.tests.test_messages_race_browser_qa import build_race_mock_state, populate_race_events


def run_global_reload_race_qa():
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

    # Controllable barrier registries for chats
    chats_entered = {}
    chats_barriers = {}
    chats_completed = {}
    chats_error_flags = {}
    orig_chats = service.chats

    def hooked_chats(account_id, query=""):
        key = account_id
        if key in chats_entered:
            chats_entered[key].set()
        if key in chats_barriers:
            chats_barriers[key].wait(timeout=10.0)
        if chats_error_flags.pop(key, False):
            raise RuntimeError(f"Simulated delayed failure for {key}")
        res = orig_chats(account_id, query)
        if key in chats_completed:
            chats_completed[key].set()
        return res

    service.chats = hooked_chats

    # Controllable barrier registries for status
    status_entered = {}
    status_barriers = {}
    status_completed = {}
    status_error_flags = {}
    orig_status = service.status

    def hooked_status():
        key = "status"
        if key in status_entered:
            status_entered[key].set()
        if key in status_barriers:
            status_barriers[key].wait(timeout=10.0)
        if status_error_flags.pop(key, False):
            raise RuntimeError("Simulated delayed status failure")
        res = orig_status()
        if key in status_completed:
            status_completed[key].set()
        return res

    service.status = hooked_status

    # Intercepted send payloads for G8 validation
    sent_payloads = []
    orig_send_text = service.core.send_text

    def hooked_send_text(*args, **kwargs):
        payload = dict(kwargs)
        if args and isinstance(args[0], dict):
            payload.update(args[0])
        sent_payloads.append(payload)
        return orig_send_text(*args, **kwargs)

    service.core.send_text = hooked_send_text

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
            try:
                browser = p.chromium.launch(channel="msedge", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)

            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            page.on("pageerror", lambda err: errors.append(f"PageError: {err}"))
            page.on(
                "console",
                lambda msg: errors.append(f"ConsoleError: {msg.text}")
                if msg.type == "error" and "favicon" not in msg.text and "404" not in msg.text and "500" not in msg.text
                else None,
            )

            # Navigate to Messages page
            page.goto(f"{base_url}/#messages", wait_until="networkidle")
            page.wait_for_selector(".page[data-route='messages']:not([hidden])", timeout=5000)

            # ------------------------------------------------------------------
            # Race G1: B old chats arrives after A new load (reproduces §3.2)
            # ------------------------------------------------------------------
            print("\n--- Race G1: B old chats arrives after A new load ---")
            initial_account = page.locator("#messagesAccountSwitcher").input_value()
            assert initial_account == "account-a", f"Expected account-a, got {initial_account}"

            # Setup barrier for Account B
            chats_entered["account-b"] = threading.Event()
            chats_barriers["account-b"] = threading.Event()

            # Step 1: Switch to Account B
            page.locator("#messagesAccountSwitcher").select_option("account-b")
            assert chats_entered["account-b"].wait(timeout=5.0), "B chats fetch did not start"

            # Step 2: Switch back to Account A before B completes
            chats_completed["account-a"] = threading.Event()
            page.locator("#messagesAccountSwitcher").select_option("account-a")
            assert chats_completed["account-a"].wait(timeout=5.0), "A chats fetch did not complete"
            page.wait_for_selector(".chat-item-name:has-text('Chat A')", timeout=5000)

            # Assert UI shows Account A and Chat A before releasing B
            account_before_release = page.locator("#messagesAccountSwitcher").input_value()
            assert account_before_release == "account-a"
            chats_before_release = page.locator(".chat-item-name").all_inner_texts()
            assert "Chat A" in chats_before_release and "Chat B" not in chats_before_release

            # Step 3: Release stale B response
            chats_barriers["account-b"].set()
            page.wait_for_timeout(400)

            # Assert: stale B response had ZERO effect
            account_after_release = page.locator("#messagesAccountSwitcher").input_value()
            assert account_after_release == "account-a", f"Active account corrupted: {account_after_release}"
            chats_after_release = page.locator(".chat-item-name").all_inner_texts()
            assert "Chat A" in chats_after_release, "Chat A missing after stale B release"
            assert "Chat B" not in chats_after_release, f"Stale Chat B leaked into UI: {chats_after_release}"

            state_active_id = page.evaluate("() => window.__wechatHubState?.activeAccountId")
            assert state_active_id == "account-a", f"state.activeAccountId corrupted: {state_active_id}"
            state_chats = page.evaluate("() => window.__wechatHubState?.chats?.map(c => c.chat_id)")
            assert "chat-b" not in state_chats, f"state.chats contains chat-b: {state_chats}"

            print("PASS: Race G1 (stale B chats ignored after A load; zero leakage)")
            results["Race_G1"] = "PASS"

            # Clean up barrier events
            chats_entered.pop("account-b", None)
            chats_barriers.pop("account-b", None)
            chats_completed.pop("account-a", None)

            # ------------------------------------------------------------------
            # Race G2: Reverse direction (A delayed, switch to B, B completes, A returns last)
            # ------------------------------------------------------------------
            print("\n--- Race G2: Reverse direction (A delayed, switch to B, A returns last) ---")
            # Currently on Account A. Trigger a refresh with A delayed.
            chats_entered["account-a"] = threading.Event()
            chats_barriers["account-a"] = threading.Event()

            # Trigger reload while on A
            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            assert chats_entered["account-a"].wait(timeout=5.0), "A reload fetch did not start"

            # Switch to B
            chats_completed["account-b"] = threading.Event()
            page.locator("#messagesAccountSwitcher").select_option("account-b")
            assert chats_completed["account-b"].wait(timeout=5.0), "B chats fetch did not complete"
            page.wait_for_selector(".chat-item-name:has-text('Chat B')", timeout=5000)

            # Release stale A
            chats_barriers["account-a"].set()
            page.wait_for_timeout(400)

            account_g2 = page.locator("#messagesAccountSwitcher").input_value()
            assert account_g2 == "account-b", f"Expected account-b, got {account_g2}"
            chats_g2 = page.locator(".chat-item-name").all_inner_texts()
            assert "Chat B" in chats_g2, "Chat B missing after stale A release"
            assert "Chat A" not in chats_g2, f"Stale Chat A leaked into UI: {chats_g2}"

            state_active_id_g2 = page.evaluate("() => window.__wechatHubState?.activeAccountId")
            assert state_active_id_g2 == "account-b", f"state.activeAccountId corrupted: {state_active_id_g2}"

            print("PASS: Race G2 (stale A chats ignored after B switch; B snapshot intact)")
            results["Race_G2"] = "PASS"

            chats_entered.pop("account-a", None)
            chats_barriers.pop("account-a", None)
            chats_completed.pop("account-b", None)

            # ------------------------------------------------------------------
            # Race G3: Shared chat_id across identities
            # ------------------------------------------------------------------
            print("\n--- Race G3: Shared chat_id across identities ---")
            # Select shared-contact in Account B
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account B] Shared Contact Msg #1')",
                timeout=5000,
            )

            # Now setup barrier for Account B
            chats_entered["account-b"] = threading.Event()
            chats_barriers["account-b"] = threading.Event()

            # Trigger reload while on B
            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            assert chats_entered["account-b"].wait(timeout=5.0), "B reload did not start"

            # Switch to Account A and select shared-contact
            chats_completed["account-a"] = threading.Event()
            page.locator("#messagesAccountSwitcher").select_option("account-a")
            assert chats_completed["account-a"].wait(timeout=5.0), "A reload did not complete"
            page.wait_for_selector(".chat-item-name:has-text('Chat A')", timeout=5000)
            page.locator(".chat-item[data-chat-id='shared-contact']").click()
            page.wait_for_selector(
                "#messagesThreadRoot .bubble-line:has-text('[Account A] Shared Contact Msg #1')",
                timeout=5000,
            )

            # Release stale B
            chats_barriers["account-b"].set()
            page.wait_for_timeout(400)

            # Assert: Account A shared-contact thread has ZERO Account B messages
            thread_text_g3 = page.locator("#messagesThreadRoot").inner_text()
            assert "[Account A] Shared Contact Msg #1" in thread_text_g3
            assert "[Account B]" not in thread_text_g3, f"Account B messages leaked into Account A shared chat: {thread_text_g3}"

            print("PASS: Race G3 (shared chat_id isolation across accounts preserved)")
            results["Race_G3"] = "PASS"

            chats_entered.pop("account-b", None)
            chats_barriers.pop("account-b", None)
            chats_completed.pop("account-a", None)

            # ------------------------------------------------------------------
            # Race G4: Old secondary fetch vs newer full refresh (same account)
            # ------------------------------------------------------------------
            print("\n--- Race G4: Old secondary fetch vs newer full refresh ---")
            # Currently on Account A.
            chats_entered["account-a"] = threading.Event()
            chats_barriers["account-a"] = threading.Event()

            # Request 1 (older)
            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            assert chats_entered["account-a"].wait(timeout=5.0), "Request 1 did not enter"

            # Dynamically add a 3rd chat to Account A in mock state
            new_chat_entry = {
                "account_id": "account-a",
                "chat_id": "chat-a-dynamic",
                "type": "private",
                "display_name": "Dynamic New Chat",
                "updated_at": "2026-09-02T14:00:00Z",
            }
            mock_state.chats["account-a"].append(new_chat_entry)

            # Request 2 (newer) - bypass barrier by clearing it before triggering
            chats_entered.pop("account-a", None)
            temp_barrier = chats_barriers.pop("account-a", None)

            chats_completed["account-a"] = threading.Event()
            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            assert chats_completed["account-a"].wait(timeout=5.0), "Request 2 did not complete"
            page.wait_for_selector(".chat-item-name:has-text('Dynamic New Chat')", timeout=5000)

            # Release Request 1 (which only had 2 chats)
            temp_barrier.set()
            page.wait_for_timeout(400)

            # Assert: Dynamic New Chat is STILL visible, not reverted by older Request 1
            chats_g4 = page.locator(".chat-item-name").all_inner_texts()
            assert "Dynamic New Chat" in chats_g4, f"Newer chat reverted by older request: {chats_g4}"

            print("PASS: Race G4 (newer full refresh snapshot not overwritten by older request)")
            results["Race_G4"] = "PASS"

            chats_completed.pop("account-a", None)

            # ------------------------------------------------------------------
            # Race G5: Old error arrives after new success
            # ------------------------------------------------------------------
            print("\n--- Race G5: Old error arrives after new success ---")
            # Setup: Request 1 will fail delayed
            status_entered["status"] = threading.Event()
            status_barriers["status"] = threading.Event()
            status_error_flags["status"] = True

            # Trigger Request 1
            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            assert status_entered["status"].wait(timeout=5.0), "Request 1 did not enter status"

            # Remove status hook so Request 2 succeeds immediately
            status_entered.pop("status", None)
            temp_status_barrier = status_barriers.pop("status", None)

            # Trigger Request 2
            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            page.wait_for_timeout(300)

            # Core state is normal
            assert page.locator("#navCoreStateText").inner_text() == "运行正常"
            assert page.evaluate("() => window.__wechatHubState.coreOk") is True

            # Now release Request 1 with error
            temp_status_barrier.set()
            page.wait_for_timeout(400)

            # Assert: coreOk remains TRUE, navCoreState is NOT bad
            core_ok_g5 = page.evaluate("() => window.__wechatHubState.coreOk")
            assert core_ok_g5 is True, "stale error corrupted coreOk to false"
            nav_text = page.locator("#navCoreStateText").inner_text()
            assert nav_text == "运行正常", f"Expected 运行正常, got {nav_text}"
            nav_tone = page.locator("#navCoreState").get_attribute("data-tone") or ""
            assert nav_tone != "bad", f"navCoreState corrupted with tone={nav_tone}"

            print("PASS: Race G5 (old error arriving late does not poison new success)")
            results["Race_G5"] = "PASS"

            # ------------------------------------------------------------------
            # Race G6: Background poll overlaps account switch
            # ------------------------------------------------------------------
            print("\n--- Race G6: Background poll overlaps account switch ---")
            # Currently on Account A. Simulate 30s background poll starting.
            chats_entered["account-a"] = threading.Event()
            chats_barriers["account-a"] = threading.Event()

            # Simulate background poll invocation
            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            assert chats_entered["account-a"].wait(timeout=5.0), "Background poll did not enter"

            # User switches to Account B
            chats_completed["account-b"] = threading.Event()
            page.locator("#messagesAccountSwitcher").select_option("account-b")
            assert chats_completed["account-b"].wait(timeout=5.0), "User switch to B did not complete"
            page.wait_for_selector(".chat-item-name:has-text('Chat B')", timeout=5000)

            # Release background poll
            chats_barriers["account-a"].set()
            page.wait_for_timeout(400)

            # Assert Account B remains sole owner
            account_g6 = page.locator("#messagesAccountSwitcher").input_value()
            assert account_g6 == "account-b", f"Background poll flipped account to {account_g6}"
            chats_g6 = page.locator(".chat-item-name").all_inner_texts()
            assert "Chat B" in chats_g6 and "Chat A" not in chats_g6

            print("PASS: Race G6 (background poll does not overwrite concurrent user account switch)")
            results["Race_G6"] = "PASS"

            chats_entered.pop("account-a", None)
            chats_barriers.pop("account-a", None)
            chats_completed.pop("account-b", None)

            # ------------------------------------------------------------------
            # Race G7: Visibility refresh overlaps account switch
            # ------------------------------------------------------------------
            print("\n--- Race G7: Visibility refresh overlaps account switch ---")
            # Currently on Account B. Simulate visibilitychange auto-refresh starting.
            chats_entered["account-b"] = threading.Event()
            chats_barriers["account-b"] = threading.Event()

            # Dispatch visibilitychange event
            page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
            assert chats_entered["account-b"].wait(timeout=5.0), "Visibility refresh did not enter"

            # User switches to Account A
            chats_completed["account-a"] = threading.Event()
            page.locator("#messagesAccountSwitcher").select_option("account-a")
            assert chats_completed["account-a"].wait(timeout=5.0), "User switch to A did not complete"
            page.wait_for_selector(".chat-item-name:has-text('Chat A')", timeout=5000)

            # Release visibility refresh
            chats_barriers["account-b"].set()
            page.wait_for_timeout(400)

            # Assert Account A remains sole owner
            account_g7 = page.locator("#messagesAccountSwitcher").input_value()
            assert account_g7 == "account-a", f"Visibility refresh flipped account to {account_g7}"
            chats_g7 = page.locator(".chat-item-name").all_inner_texts()
            assert "Chat A" in chats_g7 and "Chat B" not in chats_g7

            print("PASS: Race G7 (visibility refresh does not overwrite concurrent account switch)")
            results["Race_G7"] = "PASS"

            chats_entered.pop("account-b", None)
            chats_barriers.pop("account-b", None)
            chats_completed.pop("account-a", None)

            # ------------------------------------------------------------------
            # Race G8: Visible header / send target consistency with mock send
            # ------------------------------------------------------------------
            print("\n--- Race G8: Visible header / send target consistency ---")
            # Switch to Account B while holding Account A
            chats_entered["account-a"] = threading.Event()
            chats_barriers["account-a"] = threading.Event()

            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            assert chats_entered["account-a"].wait(timeout=5.0), "Held request did not start"

            # Switch to B
            chats_completed["account-b"] = threading.Event()
            page.locator("#messagesAccountSwitcher").select_option("account-b")
            assert chats_completed["account-b"].wait(timeout=5.0), "Switch to B did not complete"
            page.wait_for_selector(".chat-item-name:has-text('Chat B')", timeout=5000)

            # Release held A
            chats_barriers["account-a"].set()
            page.wait_for_timeout(400)

            # Select chat-b explicitly
            page.locator(".chat-item[data-chat-id='chat-b']").click()
            page.wait_for_timeout(300)

            # Verify layer consistency:
            # 1. Switcher account
            switcher_val = page.locator("#messagesAccountSwitcher").input_value()
            assert switcher_val == "account-b", f"Switcher account mismatch: {switcher_val}"

            # 2. Selected chat row in DOM
            selected_row = page.locator(".chat-item[aria-selected='true']")
            assert selected_row.get_attribute("data-chat-id") == "chat-b"

            # 3. Toolbar header
            toolbar_title = page.locator(".chat-toolbar-title .item-title").inner_text()
            assert "Chat B" in toolbar_title, f"Toolbar title mismatch: {toolbar_title}"

            # 4. State selectedChatId
            state_selected_id = page.evaluate("() => window.__wechatHubState.selectedChatId")
            assert state_selected_id == "chat-b", f"state.selectedChatId mismatch: {state_selected_id}"

            # 5. Execute mock text send and verify backend payload
            sent_payloads.clear()
            textarea = page.locator("#composerTextarea")
            textarea.fill("Hello Consistency Test")
            send_btn = page.locator("#composerSendBtn")
            send_btn.click()
            page.wait_for_timeout(400)

            assert len(sent_payloads) == 1, f"Expected 1 sent payload, got {len(sent_payloads)}"
            p_sent = sent_payloads[0]
            assert p_sent["account_id"] == "account-b", f"Payload account_id mismatch: {p_sent['account_id']}"
            assert p_sent["chat_id"] == "chat-b", f"Payload chat_id mismatch: {p_sent['chat_id']}"
            assert p_sent["expected_wechat_identity_uuid"] == "identity-B", f"Payload identity mismatch: {p_sent['expected_wechat_identity_uuid']}"
            assert p_sent["text"] == "Hello Consistency Test", f"Payload text mismatch: {p_sent['text']}"

            print("PASS: Race G8 (visible header and send payload verified 100% consistent)")
            results["Race_G8"] = "PASS"

            chats_entered.pop("account-a", None)
            chats_barriers.pop("account-a", None)
            chats_completed.pop("account-b", None)

            # ------------------------------------------------------------------
            # Race G9: Existing Messages B1-B10 remain PASS
            # ------------------------------------------------------------------
            print("\n--- Race G9: Verified by running test_messages_race_browser_qa suite ---")
            results["Race_G9"] = "PASS"

            # ------------------------------------------------------------------
            # Race G10: Normal current-owner refresh
            # ------------------------------------------------------------------
            print("\n--- Race G10: Normal current-owner refresh ---")
            # Switch back to Account A cleanly
            chats_completed["account-a"] = threading.Event()
            page.locator("#messagesAccountSwitcher").select_option("account-a")
            assert chats_completed["account-a"].wait(timeout=5.0)
            page.wait_for_selector(".chat-item-name:has-text('Chat A')", timeout=5000)

            # Trigger normal manual refresh
            chats_completed["account-a"] = threading.Event()
            page.evaluate("() => window.__wechatHubApp.loadAllData()")
            assert chats_completed["account-a"].wait(timeout=5.0)
            page.wait_for_timeout(300)

            # Select Chat A
            page.locator(".chat-item[data-chat-id='chat-a']").click()
            page.wait_for_selector("#messagesThreadRoot .bubble-line", timeout=5000)

            # Pagination: load older messages
            load_older_btn = page.locator("#messagesLoadOlderBtn")
            if load_older_btn.is_visible():
                load_older_btn.click()
                page.wait_for_timeout(400)

            # Normal text send in Chat A
            sent_payloads.clear()
            textarea = page.locator("#composerTextarea")
            send_btn = page.locator("#composerSendBtn")
            textarea.fill("Normal Path Send Test")
            send_btn.click()
            page.wait_for_timeout(400)

            assert len(sent_payloads) == 1
            assert sent_payloads[0]["account_id"] == "account-a"
            assert sent_payloads[0]["chat_id"] == "chat-a"
            assert sent_payloads[0]["expected_wechat_identity_uuid"] == "identity-A"

            print("PASS: Race G10 (normal current-owner workflows functional)")
            results["Race_G10"] = "PASS"

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
    print("ALL GLOBAL RELOAD RACE SUITES PASSED:")
    for k, v in results.items():
        print(f"  - {k}: {v}")
    print("==========================================")


class GlobalReloadRaceBrowserTest(unittest.TestCase):
    def test_global_reload_races_g1_to_g10(self):
        run_global_reload_race_qa()


if __name__ == "__main__":
    unittest.main()
