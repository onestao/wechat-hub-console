from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


LOCAL_MOCK_APP = Path(__file__).resolve().parent / "mock_core.py"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
STACK_MOCK_APP = PROJECT_ROOT / "stack" / "mock-core" / "app.py"
MOCK_APP = LOCAL_MOCK_APP if LOCAL_MOCK_APP.is_file() else STACK_MOCK_APP

spec = importlib.util.spec_from_file_location("wechat_hub_mock_core", MOCK_APP)
if spec is None or spec.loader is None:  # pragma: no cover
    raise RuntimeError(f"Unable to load Mock Core from {MOCK_APP}")
mock_core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock_core)

from wechat_console.app import ConsoleService, create_server  # noqa: E402
from wechat_console.core_client import CoreApiError, CoreClient  # noqa: E402


class ConsoleIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mock_server = mock_core.create_server("127.0.0.1", 0, mock_core.MockCoreState())
        cls.core_url = f"http://127.0.0.1:{cls.mock_server.server_port}"
        cls.mock_thread = threading.Thread(target=cls.mock_server.serve_forever, daemon=True)
        cls.mock_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        cls.mock_thread.join(timeout=2)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = ConsoleService(
            core_url=self.core_url,
            db_path=root / "console.sqlite",
            archive_dir=root / "saved-attachments",
        )

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    def test_core_contract_and_account_chat_access(self) -> None:
        client = CoreClient(self.core_url)
        self.assertEqual(client.health()["contract_version"], 1)
        accounts = client.accounts()
        self.assertEqual({row["account_id"] for row in accounts}, {"account-alpha", "account-beta"})
        chats = client.chats("account-alpha")
        self.assertGreaterEqual(len(chats["chats"]), 2)
        runtime = client.runtime_accounts()
        self.assertEqual({row["account_id"] for row in runtime["accounts"]}, {"account-alpha", "account-beta"})

    def test_runtime_management_status_and_lifecycle(self) -> None:
        status = self.service.status()
        self.assertTrue(status["runtime_management"]["ok"])
        self.assertTrue(status["runtime_management"]["registry_hot_reload"])
        created = self.service.core.create_runtime_account(
            account_id="account-console-test",
            display_name="Console Test",
            start=True,
        )
        self.assertEqual(created["registry_reload"]["added"], ["account-console-test"])
        started = self.service.core.runtime_login_start("account-console-test")
        self.assertEqual(started["login_flow_state"], "waiting_for_scan")
        login = self.service.core.runtime_login("account-console-test")
        self.assertEqual(login["state"], "waiting")
        self.assertTrue(login["snapshot_available"])
        snapshot, mime_type = self.service.core.runtime_login_snapshot("account-console-test")
        self.assertTrue(snapshot.startswith(b"\x89PNG"))
        self.assertEqual(mime_type, "image/png")
        desktop = self.service.core.runtime_desktop("account-console-test")
        self.assertEqual(desktop["runtime_provider"], "agent_wechat")
        self.assertEqual(desktop["port"], 17892)
        self.assertNotIn("token=", desktop["path"])
        self.assertIn("/desktop/", desktop["path"])
        static_dir = Path(__file__).resolve().parents[1] / "static"
        capabilities_js = (static_dir / "js" / "capabilities.js").read_text(encoding="utf-8")
        self.assertIn("推荐模式（Beta）", capabilities_js)
        self.assertIn("AgentWechat", capabilities_js)
        login_flow_js = (static_dir / "js" / "components" / "login-flow.js").read_text(encoding="utf-8")
        self.assertIn("startLogin", login_flow_js)
        api_js = (static_dir / "js" / "api.js").read_text(encoding="utf-8")
        self.assertIn("/login", api_js)
        stopped = self.service.core.runtime_account_action("account-console-test", "stop")
        self.assertFalse(stopped["status"]["running"])
        removed = self.service.core.delete_runtime_account("account-console-test")
        self.assertEqual(removed["removed"], "account-console-test")

    def test_event_sync_projects_messages_and_persists_cursor(self) -> None:
        result = self.service.sync_events_once(max_pages=5)
        self.assertTrue(result["ok"])
        self.assertEqual(result["events"], 3)
        self.assertEqual(self.service.store.cursor(), "3")
        messages = self.service.store.list_messages(limit=20)
        self.assertEqual({row["message_id"] for row in messages}, {"alpha-msg-1", "beta-msg-1"})
        beta = self.service.store.get_message("account-beta", "beta-msg-1")
        self.assertIsNotNone(beta)
        self.assertEqual(beta["media_id"], "media-image-1")

        repeated = self.service.sync_events_once(max_pages=5)
        self.assertTrue(repeated["ok"])
        self.assertEqual(repeated["events"], 0)
        self.assertEqual(len(self.service.store.list_messages(limit=20)), 2)

    def test_saved_message_snapshot_note_tags_and_permanent_archive(self) -> None:
        self.service.sync_events_once(max_pages=5)
        item = self.service.save_message(
            {
                "account_id": "account-beta",
                "chat_id": "beta-private-1",
                "message_id": "beta-msg-1",
                "title": "Keep image",
                "note": "first note",
                "tags": ["research", "image", "research"],
            }
        )
        self.assertEqual(item["snapshot"]["message_id"], "beta-msg-1")
        self.assertEqual(item["note"], "first note")
        self.assertEqual(item["tags"], ["research", "image"])
        self.assertEqual(len(item["media"]), 1)
        media = item["media"][0]
        self.assertEqual(media["status"], "archived")
        archived = self.service.store.archived_media_bytes(media["saved_media_id"])
        self.assertIsNotNone(archived)
        self.assertTrue(archived[0].startswith(b"\x89PNG"))

        # Permanent archive remains readable from Console storage with Core absent.
        offline = ConsoleService(
            core_url="http://127.0.0.1:1",
            db_path=self.service.store.db_path,
            archive_dir=self.service.store.archive_dir,
            core_timeout=0.05,
        )
        try:
            persisted = offline.store.archived_media_bytes(media["saved_media_id"])
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted[0], archived[0])
        finally:
            offline.stop()

        conn = sqlite3.connect(self.service.store.db_path)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        self.assertIn("saved_messages", tables)
        self.assertIn("saved_message_media", tables)

    def test_duplicate_save_keeps_initial_snapshot_but_updates_annotations(self) -> None:
        self.service.sync_events_once(max_pages=5)
        first = self.service.save_message(
            {
                "account_id": "account-alpha",
                "chat_id": "alpha-private-1",
                "message_id": "alpha-msg-1",
                "title": "First",
                "note": "old note",
                "tags": "one",
            }
        )
        original_snapshot = json.dumps(first["snapshot"], sort_keys=True)
        second = self.service.save_message(
            {
                "account_id": "account-alpha",
                "chat_id": "alpha-private-1",
                "message_id": "alpha-msg-1",
                "title": "Updated",
                "note": "new note",
                "tags": "two, three",
                "snapshot": {
                    "account_id": "account-alpha",
                    "chat_id": "alpha-private-1",
                    "message_id": "alpha-msg-1",
                    "type": "text",
                    "text": "mutated source should not replace snapshot",
                },
            }
        )
        self.assertEqual(first["saved_message_id"], second["saved_message_id"])
        self.assertEqual(json.dumps(second["snapshot"], sort_keys=True), original_snapshot)
        self.assertEqual(second["note"], "new note")
        self.assertEqual(second["tags"], ["two", "three"])
        self.assertEqual(self.service.store.saved_count(), 1)

    def test_text_send_uses_core_outbox_idempotency(self) -> None:
        client = self.service.core
        first = client.send_text(
            account_id="account-alpha",
            chat_id="alpha-private-1",
            text="console hello",
            client_request_id="console-test-1",
            idempotency_key="console-same-key",
        )
        second = client.send_text(
            account_id="account-alpha",
            chat_id="alpha-private-1",
            text="console hello",
            client_request_id="console-test-1",
            idempotency_key="console-same-key",
        )
        self.assertEqual(first["send_id"], second["send_id"])
        self.assertEqual(first["status"], "accepted")

    def test_send_projection_distinguishes_submitted_confirmed_and_uncertain(self) -> None:
        accepted = {
            "send_id": "send-console-state",
            "status": "accepted",
            "kind": "text",
            "account_id": "account-alpha",
            "chat_id": "alpha-private-1",
            "accepted_at": "2026-09-02T00:00:00Z",
        }
        self.service.store.record_send_receipt(accepted)
        self.service.store.ingest_events(
            [
                {
                    "event_id": "send-state-submitted",
                    "cursor": "100",
                    "account_id": "account-alpha",
                    "event_type": "send.updated",
                    "occurred_at": "2026-09-02T00:00:01Z",
                    "payload": {
                        "send": {
                            **accepted,
                            "status": "submitted",
                            "delivery_certainty": "pending_confirmation",
                            "automatic_retry": False,
                        }
                    },
                }
            ],
            "100",
        )
        submitted = self.service.store.get_send("send-console-state")
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(submitted["delivery_certainty"], "pending_confirmation")
        self.assertFalse(submitted["automatic_retry"])

        self.service.store.ingest_events(
            [
                {
                    "event_id": "send-state-sent",
                    "cursor": "101",
                    "account_id": "account-alpha",
                    "event_type": "send.updated",
                    "occurred_at": "2026-09-02T00:00:02Z",
                    "payload": {
                        "send": {
                            **accepted,
                            "status": "sent",
                            "echo_message_id": "wechat-confirmed-1",
                            "delivery_certainty": "confirmed",
                            "automatic_retry": False,
                        }
                    },
                }
            ],
            "101",
        )
        sent = self.service.store.get_send("send-console-state")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(sent["echo_message_id"], "wechat-confirmed-1")

        uncertain = {
            **accepted,
            "send_id": "send-console-uncertain",
            "status": "submitted",
            "delivery_certainty": "pending_confirmation",
            "automatic_retry": False,
        }
        self.service.store.record_send_receipt(uncertain)
        self.service.store.ingest_events(
            [
                {
                    "event_id": "send-state-uncertain",
                    "cursor": "102",
                    "account_id": "account-alpha",
                    "event_type": "send.updated",
                    "occurred_at": "2026-09-02T00:02:02Z",
                    "payload": {
                        "send": {
                            **uncertain,
                            "status": "uncertain",
                            "delivery_certainty": "unknown",
                        },
                        "details": {"delivery_certainty": "unknown", "automatic_retry": False},
                    },
                }
            ],
            "102",
        )
        unknown = self.service.store.get_send("send-console-uncertain")
        self.assertEqual(unknown["status"], "uncertain")
        self.assertEqual(unknown["delivery_certainty"], "unknown")
        self.assertFalse(unknown["automatic_retry"])

        messages_js = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "views" / "messages.js"
        ).read_text(encoding="utf-8")
        self.assertIn("已提交，等待微信确认", messages_js)
        self.assertIn("已确认发送", messages_js)

    def test_http_surface_with_mock_core(self) -> None:
        self.service.sync_events_once(max_pages=5)
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            status, payload = self.request(base + "/api/status")
            self.assertEqual(status, 200)
            self.assertTrue(payload["core"]["ok"])
            self.assertEqual(payload["contract_version"], 1)

            _, accounts = self.request(base + "/api/accounts")
            self.assertEqual(len(accounts["accounts"]), 2)

            _, runtime = self.request(base + "/api/runtime/accounts")
            self.assertEqual(len(runtime["accounts"]), 2)
            status, created_account = self.request(
                base + "/api/runtime/accounts",
                method="POST",
                payload={"account_id": "account-http-test", "display_name": "HTTP Test", "start": True},
            )
            self.assertEqual(status, 201)
            self.assertEqual(created_account["registry_reload"]["added"], ["account-http-test"])
            _, login = self.request(base + "/api/runtime/accounts/account-http-test/login")
            self.assertEqual(login["state"], "waiting")
            with urllib.request.urlopen(base + "/api/runtime/accounts/account-http-test/login/snapshot", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get("Cache-Control"), "no-store, max-age=0")
                self.assertTrue(response.read().startswith(b"\x89PNG"))
            _, stopped = self.request(
                base + "/api/runtime/accounts/account-http-test/stop",
                method="POST",
                payload={},
            )
            self.assertFalse(stopped["status"]["running"])
            _, removed = self.request(base + "/api/runtime/accounts/account-http-test", method="DELETE")
            self.assertEqual(removed["removed"], "account-http-test")

            _, messages = self.request(base + "/api/messages?account_id=account-beta")
            self.assertEqual(messages["messages"][0]["message_id"], "beta-msg-1")

            status, text_send = self.request(
                base + "/api/send/text",
                method="POST",
                payload={
                    "account_id": "account-alpha",
                    "chat_id": "alpha-private-1",
                    "text": "http test message",
                    "client_request_id": "http-test-send-1",
                },
            )
            self.assertEqual(status, 202)
            self.assertIn("send_id", text_send)
            send_id = text_send["send_id"]

            status, send_status = self.request(base + f"/api/sends/{send_id}")
            self.assertEqual(status, 200)
            self.assertEqual(send_status["send_id"], send_id)
            self.assertIn(send_status["status"], {"accepted", "queued", "sending", "submitted", "sent", "failed", "uncertain"})

            status, img_send = self.request(
                base + "/api/send/image",
                method="POST",
                payload={
                    "account_id": "account-alpha",
                    "chat_id": "alpha-private-1",
                    "content_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                    "filename": "pixel.png",
                    "mime_type": "image/png",
                    "client_request_id": "http-test-img-1",
                },
            )
            self.assertEqual(status, 202)
            self.assertIn("send_id", img_send)
            self.assertEqual(img_send.get("kind"), "image")

            status, file_send = self.request(
                base + "/api/send/file",
                method="POST",
                payload={
                    "account_id": "account-alpha",
                    "chat_id": "alpha-private-1",
                    "content_base64": "aGVsbG8gd29ybGQ=",
                    "filename": "hello.txt",
                    "mime_type": "text/plain",
                    "client_request_id": "http-test-file-1",
                },
            )
            self.assertEqual(status, 202)
            self.assertIn("send_id", file_send)
            self.assertEqual(file_send.get("kind"), "file")

            api_js = (Path(__file__).resolve().parents[1] / "static" / "js" / "api.js").read_text(encoding="utf-8")
            self.assertIn("sendStatus:", api_js)
            self.assertIn("sendImage:", api_js)
            self.assertIn("sendFile:", api_js)

            status, saved = self.request(
                base + "/api/saved",
                method="POST",
                payload={
                    "account_id": "account-beta",
                    "chat_id": "beta-private-1",
                    "message_id": "beta-msg-1",
                    "note": "via http",
                    "tags": "api, durable",
                },
            )
            self.assertEqual(status, 201)
            self.assertEqual(saved["note"], "via http")
            media_id = saved["media"][0]["saved_media_id"]

            get_status, saved_list = self.request(base + "/api/saved")
            self.assertEqual(get_status, 200)
            self.assertIn("items", saved_list)
            self.assertIsInstance(saved_list["items"], list)
            self.assertEqual(len(saved_list["items"]), 1)
            self.assertEqual(saved_list["items"][0]["saved_message_id"], saved["saved_message_id"])

            app_js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
            self.assertIn("savedRes.value.items", app_js)

            with urllib.request.urlopen(base + f"/api/saved-media/{media_id}", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read().startswith(b"\x89PNG"))

            # Test direct /api/media/{media_id} passthrough: ready (200) vs pending (202)
            with urllib.request.urlopen(base + "/api/media/media-image-1?account_id=account-beta", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get("Content-Type"), "image/png")
                self.assertTrue(response.read().startswith(b"\x89PNG"))

            with urllib.request.urlopen(base + "/api/media/media-pending-1?account_id=account-beta", timeout=2) as response:
                self.assertEqual(response.status, 202)
                self.assertEqual(response.headers.get("Content-Type"), "application/json")
                pending_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(pending_payload["error"]["code"], "media_pending")

            _, deleted = self.request(base + f"/api/saved/{saved['saved_message_id']}", method="DELETE")
            self.assertTrue(deleted["ok"])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base + f"/api/saved-media/{media_id}", timeout=2)
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @staticmethod
    def request(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, method=method, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())



    def test_messages_cursor_pagination_and_ordering(self) -> None:
        account_id = "account-alpha"
        chat_id = "alpha-pagination-chat"
        events = []
        for i in range(1, 6):
            # i=4 and i=5 share the same minute to test (created_at, message_id) tie-breaking
            minute = 3 if i >= 4 else i
            events.append(
                {
                    "event_id": f"evt-page-{i}",
                    "cursor": str(200 + i),
                    "account_id": account_id,
                    "event_type": "message.created",
                    "occurred_at": f"2026-09-02T10:0{minute}:00Z",
                    "payload": {
                        "message": {
                            "account_id": account_id,
                            "message_id": f"msg-page-{i}",
                            "chat_id": chat_id,
                            "type": "text",
                            "direction": "incoming",
                            "created_at": f"2026-09-02T10:0{minute}:00Z",
                            "text": f"Message {i}",
                            "author": {"member_id": "alice", "display_name": "Alice"},
                        }
                    },
                }
            )
        self.service.store.ingest_events(events, "210")

        # Page 1: limit 2
        page1 = self.service.store.list_messages(account_id=account_id, chat_id=chat_id, limit=2)
        self.assertEqual(len(page1), 2)
        self.assertEqual(page1[0]["message_id"], "msg-page-5")
        self.assertEqual(page1[1]["message_id"], "msg-page-4")
        self.assertTrue(page1.has_more)
        self.assertTrue(bool(page1.next_cursor))

        # Page 2: limit 2 using cursor
        page2 = self.service.store.list_messages(
            account_id=account_id, chat_id=chat_id, limit=2, before=page1.next_cursor
        )
        self.assertEqual(len(page2), 2)
        self.assertEqual(page2[0]["message_id"], "msg-page-3")
        self.assertEqual(page2[1]["message_id"], "msg-page-2")
        self.assertTrue(page2.has_more)
        self.assertTrue(bool(page2.next_cursor))

        # Page 3: limit 2 using cursor (last remaining message)
        page3 = self.service.store.list_messages(
            account_id=account_id, chat_id=chat_id, limit=2, before=page2.next_cursor
        )
        self.assertEqual(len(page3), 1)
        self.assertEqual(page3[0]["message_id"], "msg-page-1")
        self.assertFalse(page3.has_more)
        self.assertEqual(page3.next_cursor, "")

    def test_scoped_message_querying_by_chat_and_identity(self) -> None:
        events = [
            {
                "event_id": "evt-scope-1",
                "cursor": "301",
                "account_id": "account-alpha",
                "event_type": "message.created",
                "occurred_at": "2026-09-02T11:00:00Z",
                "payload": {
                    "instance_uuid": "inst-1",
                    "wechat_identity_uuid": "ident-1",
                    "message": {
                        "account_id": "account-alpha",
                        "instance_uuid": "inst-1",
                        "wechat_identity_uuid": "ident-1",
                        "message_id": "scope-msg-1",
                        "chat_id": "chat-a",
                        "type": "text",
                        "created_at": "2026-09-02T11:00:00Z",
                        "text": "Chat A message",
                    },
                },
            },
            {
                "event_id": "evt-scope-2",
                "cursor": "302",
                "account_id": "account-alpha",
                "event_type": "message.created",
                "occurred_at": "2026-09-02T11:01:00Z",
                "payload": {
                    "instance_uuid": "inst-1",
                    "wechat_identity_uuid": "ident-1",
                    "message": {
                        "account_id": "account-alpha",
                        "instance_uuid": "inst-1",
                        "wechat_identity_uuid": "ident-1",
                        "message_id": "scope-msg-2",
                        "chat_id": "chat-b",
                        "type": "text",
                        "created_at": "2026-09-02T11:01:00Z",
                        "text": "Chat B message",
                    },
                },
            },
            {
                "event_id": "evt-scope-3",
                "cursor": "303",
                "account_id": "account-beta",
                "event_type": "message.created",
                "occurred_at": "2026-09-02T11:02:00Z",
                "payload": {
                    "instance_uuid": "inst-2",
                    "wechat_identity_uuid": "ident-2",
                    "message": {
                        "account_id": "account-beta",
                        "instance_uuid": "inst-2",
                        "wechat_identity_uuid": "ident-2",
                        "message_id": "scope-msg-3",
                        "chat_id": "chat-a",
                        "type": "text",
                        "created_at": "2026-09-02T11:02:00Z",
                        "text": "Identity 2 message in Chat A",
                    },
                },
            },
        ]
        self.service.store.ingest_events(events, "310")

        # Scope by chat_id only
        chat_a_msgs = self.service.store.list_messages(chat_id="chat-a")
        self.assertEqual({m["message_id"] for m in chat_a_msgs}, {"scope-msg-1", "scope-msg-3"})

        chat_b_msgs = self.service.store.list_messages(chat_id="chat-b")
        self.assertEqual({m["message_id"] for m in chat_b_msgs}, {"scope-msg-2"})

        # Scope by identity + chat
        ident1_chat_a = self.service.store.list_messages(wechat_identity_uuid="ident-1", chat_id="chat-a")
        self.assertEqual([m["message_id"] for m in ident1_chat_a], ["scope-msg-1"])

        ident2_chat_a = self.service.store.list_messages(wechat_identity_uuid="ident-2", chat_id="chat-a")
        self.assertEqual([m["message_id"] for m in ident2_chat_a], ["scope-msg-3"])

    def test_console_scope_intersection_store_and_http_api(self) -> None:
        """Verify multi-scope intersection on Console Store and HTTP API:
        account_id AND instance_uuid AND wechat_identity_uuid AND chat_id.
        Specifically tests that wrong account / instance with correct identity returns 0.
        """
        acc_target = "acc-target"
        inst_target = "inst-target-uuid"
        ident_target = "ident-target-uuid"
        chat_target = "chat-target@chatroom"
        msg_target_id = "msg-target-scope-001"

        # Ingest target message
        self.service.store.ingest_events(
            [
                {
                    "event_id": "evt-scope-intersection-1",
                    "cursor": "401",
                    "account_id": acc_target,
                    "event_type": "message.created",
                    "occurred_at": "2026-09-20T12:00:00Z",
                    "payload": {
                        "instance_uuid": inst_target,
                        "wechat_identity_uuid": ident_target,
                        "message": {
                            "account_id": acc_target,
                            "instance_uuid": inst_target,
                            "wechat_identity_uuid": ident_target,
                            "message_id": msg_target_id,
                            "chat_id": chat_target,
                            "type": "text",
                            "created_at": "2026-09-20T12:00:00Z",
                            "text": "Target scoped intersection message",
                        },
                    },
                }
            ],
            "402",
        )

        # 1. Direct Store list_messages tests
        # Correct account + correct instance + correct identity -> 1 message
        page = self.service.store.list_messages(
            account_id=acc_target,
            instance_uuid=inst_target,
            wechat_identity_uuid=ident_target,
            chat_id=chat_target,
        )
        msgs = list(page)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["message_id"], msg_target_id)

        # Wrong account + correct instance + correct identity -> 0
        page_wrong_acc = self.service.store.list_messages(
            account_id="wrong-account",
            instance_uuid=inst_target,
            wechat_identity_uuid=ident_target,
            chat_id=chat_target,
        )
        self.assertEqual(len(list(page_wrong_acc)), 0)

        # Correct account + wrong instance + correct identity -> 0
        page_wrong_inst = self.service.store.list_messages(
            account_id=acc_target,
            instance_uuid="wrong-instance",
            wechat_identity_uuid=ident_target,
            chat_id=chat_target,
        )
        self.assertEqual(len(list(page_wrong_inst)), 0)

        # Correct account + correct instance + wrong identity -> 0
        page_wrong_ident = self.service.store.list_messages(
            account_id=acc_target,
            instance_uuid=inst_target,
            wechat_identity_uuid="wrong-identity",
            chat_id=chat_target,
        )
        self.assertEqual(len(list(page_wrong_ident)), 0)

        # 2. HTTP API GET /api/messages tests
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            # 正确 account + 正确 instance + 正确 identity -> 返回消息
            st, data = self.request(
                f"{base}/api/messages?account_id={acc_target}&instance_uuid={inst_target}&wechat_identity_uuid={ident_target}&chat_id={chat_target}"
            )
            self.assertEqual(st, 200)
            self.assertEqual(len(data.get("messages", [])), 1)
            self.assertEqual(data["messages"][0]["message_id"], msg_target_id)

            # 错误 account + 正确 instance + 正确 identity -> 0
            st, data = self.request(
                f"{base}/api/messages?account_id=wrong-account&instance_uuid={inst_target}&wechat_identity_uuid={ident_target}&chat_id={chat_target}"
            )
            self.assertEqual(st, 200)
            self.assertEqual(len(data.get("messages", [])), 0)

            # 正确 account + 错误 instance + 正确 identity -> 0
            st, data = self.request(
                f"{base}/api/messages?account_id={acc_target}&instance_uuid=wrong-instance&wechat_identity_uuid={ident_target}&chat_id={chat_target}"
            )
            self.assertEqual(st, 200)
            self.assertEqual(len(data.get("messages", [])), 0)

            # 正确 account + 正确 instance + 错误 identity -> 0
            st, data = self.request(
                f"{base}/api/messages?account_id={acc_target}&instance_uuid={inst_target}&wechat_identity_uuid=wrong-identity&chat_id={chat_target}"
            )
            self.assertEqual(st, 200)
            self.assertEqual(len(data.get("messages", [])), 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_send_forwards_expected_wechat_identity_uuid(self) -> None:
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            # Text send with expected_wechat_identity_uuid
            status, text_res = self.request(
                base + "/api/send/text",
                method="POST",
                payload={
                    "account_id": "account-alpha",
                    "chat_id": "alpha-private-1",
                    "text": "identity protected text",
                    "client_request_id": "id-send-text-1",
                    "expected_wechat_identity_uuid": "ident-expected-001",
                },
            )
            self.assertEqual(status, 202)
            last_send = self.mock_server.RequestHandlerClass.state.sends[-1]["request"]
            self.assertEqual(last_send.get("expected_wechat_identity_uuid"), "ident-expected-001")

            # Image send with expected_wechat_identity_uuid
            status, img_res = self.request(
                base + "/api/send/image",
                method="POST",
                payload={
                    "account_id": "account-alpha",
                    "chat_id": "alpha-private-1",
                    "content_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                    "filename": "pixel.png",
                    "mime_type": "image/png",
                    "client_request_id": "id-send-img-1",
                    "expected_wechat_identity_uuid": "ident-expected-001",
                },
            )
            self.assertEqual(status, 202)
            last_send = self.mock_server.RequestHandlerClass.state.sends[-1]["request"]
            self.assertEqual(last_send.get("expected_wechat_identity_uuid"), "ident-expected-001")

            # File send with expected_wechat_identity_uuid
            status, file_res = self.request(
                base + "/api/send/file",
                method="POST",
                payload={
                    "account_id": "account-alpha",
                    "chat_id": "alpha-private-1",
                    "content_base64": "aGVsbG8=",
                    "filename": "test.txt",
                    "mime_type": "text/plain",
                    "client_request_id": "id-send-file-1",
                    "expected_wechat_identity_uuid": "ident-expected-001",
                },
            )
            self.assertEqual(status, 202)
            last_send = self.mock_server.RequestHandlerClass.state.sends[-1]["request"]
            self.assertEqual(last_send.get("expected_wechat_identity_uuid"), "ident-expected-001")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_contacts_and_group_members_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ConsoleService(core_url=self.core_url, db_path=Path(tmp) / "console.sqlite", archive_dir=Path(tmp) / "archive")
            server = create_server("127.0.0.1", 0, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                # 1. Fetch contacts for account-alpha
                req = urllib.request.Request(f"{base}/api/contacts?account_id=account-alpha")
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertIn("contacts", data)
                    self.assertEqual(len(data["contacts"]), 2)
                    alice = next(c for c in data["contacts"] if c["member_id"] == "alice")
                    self.assertEqual(alice["display_name"], "Alice Wonderland")

                # 2. Search contacts
                req = urllib.request.Request(f"{base}/api/contacts?account_id=account-alpha&query=Charlie")
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(len(data["contacts"]), 1)
                    self.assertEqual(data["contacts"][0]["member_id"], "charlie")

                # 3. Group members
                req = urllib.request.Request(f"{base}/api/chats/alpha-group-1%40chatroom/members?account_id=account-alpha")
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertIn("members", data)
                    self.assertEqual(len(data["members"]), 2)
                    self.assertEqual(data["members"][0]["group_nickname"], "Alice (Leader)")

                # 4. Identity Profile
                req = urllib.request.Request(f"{base}/api/identity/profile?account_id=account-alpha")
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(data["wechat_identity_uuid"], "identity-alpha-uuid")
                    self.assertEqual(data["nickname"], "Mock User")

                # 5. Avatar proxy
                req = urllib.request.Request(f"{base}/api/avatar/alice")
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers.get_content_type(), "image/png")
                    self.assertTrue(len(resp.read()) > 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class ConsoleIdentityProfileTest(unittest.TestCase):
    """Agent C — identity v2 account/profile surfaces (C2/C3/C6)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mock_server = mock_core.create_server(
            "127.0.0.1", 0, mock_core.MockCoreState(identity_scenario=True)
        )
        cls.core_url = f"http://127.0.0.1:{cls.mock_server.server_port}"
        cls.mock_thread = threading.Thread(target=cls.mock_server.serve_forever, daemon=True)
        cls.mock_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        cls.mock_thread.join(timeout=2)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = ConsoleService(
            core_url=self.core_url,
            db_path=root / "console.sqlite",
            archive_dir=root / "saved-attachments",
        )

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    @staticmethod
    def request(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read()
                if response.headers.get_content_type() == "image/png":
                    return response.status, body
                return response.status, json.loads(body)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_account_detail_enriches_identity_profile(self) -> None:
        detail = self.service.core.account_detail("account-alpha")
        self.assertEqual(detail["identity_binding_state"], "bound")
        self.assertEqual(detail["wechat_identity_uuid"], mock_core.IDENTITY_ALPHA_UUID)
        self.assertEqual(detail["wechat_profile"]["nickname"], "科研助手小张")
        self.assertTrue(detail["wechat_profile"]["avatar_url"].startswith("http://"))
        self.assertEqual(detail["instance_uuid"], mock_core.INSTANCE_ALPHA_UUID)
        with self.assertRaises(CoreApiError) as caught:
            self.service.core.account_detail("account-ghost")
        self.assertEqual(caught.exception.status, 404)

    def test_display_name_rename_closed_loop_keeps_canonical_keys(self) -> None:
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            before = self.service.core.account_detail("account-alpha")
            status, _ = self.request(
                base + "/api/runtime/accounts/account-alpha/update",
                method="POST",
                payload={"display_name": "Alpha 新备注"},
            )
            self.assertEqual(status, 200)
            after = self.service.core.account_detail("account-alpha")
            self.assertEqual(after["display_name"], "Alpha 新备注")
            # C3 invariant: rename must not touch instance_uuid / resource_key
            self.assertEqual(after["instance_uuid"], before["instance_uuid"])
            self.assertEqual(after["resource_key"], before["resource_key"])
            self.assertEqual(after["wechat_identity_uuid"], before["wechat_identity_uuid"])

            status, body = self.request(
                base + "/api/runtime/accounts/account-alpha/update",
                method="POST",
                payload={},
            )
            self.assertEqual(status, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_confirm_switch_resolves_mismatch(self) -> None:
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            gamma = self.service.core.account_detail("account-gamma")
            self.assertEqual(gamma["identity_binding_state"], "mismatch")
            self.assertEqual(gamma["observed_wechat_user_id"], mock_core.IDENTITY_GAMMA_OBSERVED_WXID)

            status, result = self.request(
                base + "/api/runtime/accounts/account-gamma/confirm-switch",
                method="POST",
                payload={"observed_wechat_user_id": mock_core.IDENTITY_GAMMA_OBSERVED_WXID},
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["identity_binding_state"], "bound")

            resolved = self.service.core.account_detail("account-gamma")
            self.assertEqual(resolved["identity_binding_state"], "bound")
            self.assertEqual(resolved["observed_wechat_user_id"], "")
            # Confirming a switch on a bound account is rejected
            status, _ = self.request(
                base + "/api/runtime/accounts/account-alpha/confirm-switch",
                method="POST",
                payload={},
            )
            self.assertEqual(status, 409)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_avatar_proxy_serves_core_resolved_bytes_only(self) -> None:
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            status, body = self.request(base + f"/api/avatar/{mock_core.IDENTITY_ALPHA_UUID}")
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith(b"\x89PNG"))

            # No avatar for an identity without a profile → structured 404
            status, _ = self.request(base + "/api/avatar/00000000-0000-4000-8000-000000000000")
            self.assertEqual(status, 404)
            # Malformed identity ids are rejected without Core lookups
            status, _ = self.request(base + "/api/avatar/bad_id!")
            self.assertEqual(status, 404)
            # A client-supplied URL parameter is ignored: resolution is Core-only
            status, body = self.request(
                base + f"/api/avatar/{mock_core.IDENTITY_ALPHA_UUID}?url=https://evil.example/x.png"
            )
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith(b"\x89PNG"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class CoreFailureTest(unittest.TestCase):
    def test_unavailable_core_is_structured(self) -> None:
        client = CoreClient("http://127.0.0.1:1", timeout=0.05)
        with self.assertRaises(CoreApiError) as caught:
            client.health()
        self.assertEqual(caught.exception.code, "core_unavailable")


if __name__ == "__main__":
    unittest.main()
