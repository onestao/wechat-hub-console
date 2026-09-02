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


PROJECT_ROOT = Path(__file__).resolve().parents[4]
MOCK_APP = PROJECT_ROOT / "stack" / "mock-core" / "app.py"
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


class CoreFailureTest(unittest.TestCase):
    def test_unavailable_core_is_structured(self) -> None:
        client = CoreClient("http://127.0.0.1:1", timeout=0.05)
        with self.assertRaises(CoreApiError) as caught:
            client.health()
        self.assertEqual(caught.exception.code, "core_unavailable")


if __name__ == "__main__":
    unittest.main()
