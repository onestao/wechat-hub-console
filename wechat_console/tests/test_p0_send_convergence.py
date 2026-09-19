"""P0-0 Console-side regression: consumer bootstrap + send convergence + UX.

Two Factory Fresh defects are pinned here:

1. The Console never performed Core's governed consumer bootstrap, so every
   ``poll_events(consumer_id=...)`` returned ``missing_bootstrap_provenance``.
   ``core_events`` stayed empty and ``send_projection`` was only ever written
   from the POST receipt, so a send Core had already failed kept rendering as
   "正在排队发送…" forever.

2. ``/api/sends/{id}`` served the local mirror only.  It now reconciles a
   non-terminal projection against Core's authoritative receipt and always
   carries a product-facing ``display_message`` instead of an internal error.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


from wechat_console.app import ConsoleService, create_server  # noqa: E402
from wechat_console.core_client import CoreApiError  # noqa: E402


STREAM_HEAD = 73


class _FakeCoreState:
    def __init__(self) -> None:
        self.bootstrapped = False
        self.bootstrap_calls: list[dict] = []
        self.poll_calls: list[dict] = []
        self.send_status_calls: list[str] = []
        self.events: list[dict] = [
            {
                "event_id": "event-1",
                "cursor": str(STREAM_HEAD),
                "account_id": "arasial",
                "event_type": "send.updated",
                "occurred_at": "2026-09-19T10:04:00Z",
                "payload": {
                    "send": {
                        "send_id": "send-event-driven",
                        "account_id": "arasial",
                        "chat_id": "chat-1",
                        "kind": "text",
                        "status": "failed",
                        "accepted_at": "2026-09-19T10:03:58Z",
                    },
                    "error": {"code": "sender_failed", "message": "internal detail"},
                    "details": {"failure": {"code": "wechat_unavailable", "user_message": "微信客户端当前不可用，请稍后重试。"}},
                },
            }
        ]
        self.send_payload: dict = {
            "send_id": "send-stale",
            "account_id": "arasial",
            "chat_id": "chat-1",
            "kind": "text",
            "status": "failed",
            "accepted_at": "2026-09-19T10:03:58Z",
            "updated_at": "2026-09-19T10:04:00Z",
            "attempt_count": 1,
            "error_code": "wechat_unavailable",
            "error_message": "agent-wechat chat pre-open failed before submission: OSError",
            "user_message": "微信客户端当前不可用，请稍后重试。",
            "details": {"failure": {"code": "wechat_unavailable", "user_message": "微信客户端当前不可用，请稍后重试。"}},
        }
        self.send_ready = True
        self.blocked_reason = ""
        self.blocked_message = ""
        self.send_http_status = 202
        self.send_http_body: dict = {
            "send_id": "send-new",
            "account_id": "arasial",
            "chat_id": "chat-1",
            "kind": "text",
            "status": "accepted",
            "accepted_at": "2026-09-19T13:00:00Z",
        }


class _FakeCoreHandler(BaseHTTPRequestHandler):
    state: _FakeCoreState

    def log_message(self, *args) -> None:  # keep the test output clean
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _account(self) -> dict:
        state = self.state
        return {
            "account_id": "arasial",
            "display_name": "arasial",
            "state": "online",
            "runtime_provider": "agent_wechat",
            "send_ready": state.send_ready,
            "send_blocked_reason": state.blocked_reason,
            "send_blocked_message": state.blocked_message,
            "runtime": {
                "runtime_provider": "agent_wechat",
                "sender_enabled": True,
                "sender_capabilities": {"driver": "agent_wechat", "text": True, "image": True, "file": True},
            },
        }

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._json(200, {"ok": True, "service": "wechat-core", "contract_version": 1, "accounts": 1})
            return
        if path == "/v1/accounts":
            self._json(200, {"accounts": [self._account()]})
            return
        if path == "/v1/runtime/accounts":
            self._json(200, {"accounts": [], "registry_reload": {"ok": True}})
            return
        if path == "/v1/events/poll":
            self.state.poll_calls.append({"path": self.path, "bootstrapped": self.state.bootstrapped})
            if not self.state.bootstrapped:
                self._json(
                    400,
                    {
                        "error": {
                            "code": "missing_bootstrap_provenance",
                            "message": "Consumer 'wechat-console' has not executed governed bootstrap.",
                        }
                    },
                )
                return
            self._json(
                200,
                {
                    "events": list(self.state.events),
                    "next_cursor": str(STREAM_HEAD),
                    "has_more": False,
                    "stream_head_cursor": STREAM_HEAD,
                    "retention_floor_cursor": 1,
                },
            )
            return
        if path.startswith("/v1/sends/"):
            send_id = path[len("/v1/sends/") :]
            self.state.send_status_calls.append(send_id)
            payload = dict(self.state.send_payload)
            payload["send_id"] = send_id
            self._json(200, payload)
            return
        self._json(404, {"error": {"code": "not_found", "message": path}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/v1/consumers/bootstrap":
            self.state.bootstrap_calls.append(payload)
            self.state.bootstrapped = True
            self._json(
                200,
                {
                    "ok": True,
                    "consumer_id": payload.get("consumer_id"),
                    "initial_cursor": STREAM_HEAD,
                    "processed_through_cursor": STREAM_HEAD,
                    "mode": payload.get("mode"),
                    "stream_head_cursor": STREAM_HEAD,
                    "retention_floor_cursor": 1,
                    "idempotent": False,
                },
            )
            return
        if path == "/v1/events/ack":
            self._json(200, {"acked_count": len(payload.get("event_ids") or [])})
            return
        if path == "/v1/events/checkpoint":
            self._json(200, {"ok": True})
            return
        if path == "/v1/send/text":
            self._json(self.state.send_http_status, self.state.send_http_body)
            return
        self._json(404, {"error": {"code": "not_found", "message": path}})


class ConsoleSendConvergenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        state = _FakeCoreState()
        handler = type("_BoundFakeCoreHandler", (_FakeCoreHandler,), {"state": state})
        cls.state = state
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.core_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        self.state.bootstrapped = False
        self.state.bootstrap_calls.clear()
        self.state.poll_calls.clear()
        self.state.send_status_calls.clear()
        self.state.send_ready = True
        self.state.blocked_reason = ""
        self.state.blocked_message = ""
        self.state.send_http_status = 202
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

    # -- bootstrap -------------------------------------------------------

    def test_event_sync_bootstraps_once_and_then_ingests(self) -> None:
        self.assertEqual(self.service.store.cursor(), "")

        result = self.service.sync_events_once(max_pages=1)
        self.assertEqual(result["ok"], True, result)
        self.assertEqual(len(self.state.bootstrap_calls), 1)
        self.assertEqual(self.state.bootstrap_calls[0]["consumer_id"], "wechat-console")
        self.assertEqual(self.state.bootstrap_calls[0]["mode"], "at_head")
        # The server-assigned initial cursor is adopted locally, otherwise the
        # next poll would be rejected for being below it.
        self.assertEqual(self.service.store.cursor(), str(STREAM_HEAD))
        # The bootstrap retried the same page instead of dropping a cycle.
        self.assertEqual(len(self.state.poll_calls), 2)

        # Event-driven convergence: the failed send is projected locally.
        projected = self.service.store.get_send("send-event-driven")
        self.assertIsNotNone(projected)
        self.assertEqual(projected["status"], "failed")

        # A second cycle must not re-bootstrap.
        self.service.sync_events_once(max_pages=1)
        self.assertEqual(len(self.state.bootstrap_calls), 1)

    # -- send status reconciliation --------------------------------------

    def test_stale_accepted_projection_is_reconciled_from_core(self) -> None:
        self.service.store.record_send_receipt(
            {
                "send_id": "send-stale",
                "account_id": "arasial",
                "chat_id": "chat-1",
                "kind": "text",
                "status": "accepted",
                "accepted_at": "2026-09-19T10:03:58Z",
            }
        )
        self.assertEqual(self.service.store.get_send("send-stale")["status"], "accepted")

        item = self.service.send_status("send-stale")
        self.assertEqual(self.state.send_status_calls, ["send-stale"])
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["display_message"], "微信客户端当前不可用，请稍后重试。")
        # The mirror is converged, so the UI stops showing "正在排队发送…".
        self.assertEqual(self.service.store.get_send("send-stale")["status"], "failed")

    def test_terminal_projection_is_not_requeried(self) -> None:
        self.service.store.record_send_receipt(
            {
                "send_id": "send-done",
                "account_id": "arasial",
                "chat_id": "chat-1",
                "kind": "text",
                "status": "sent",
                "accepted_at": "2026-09-19T10:00:00Z",
            }
        )
        item = self.service.send_status("send-done")
        self.assertEqual(item["status"], "sent")
        self.assertEqual(item["display_message"], "")
        self.assertEqual(self.state.send_status_calls, [])

    def test_unknown_send_is_fetched_and_stored(self) -> None:
        item = self.service.send_status("send-never-seen")
        self.assertEqual(item["status"], "failed")
        self.assertTrue(item["display_message"])
        self.assertIsNotNone(self.service.store.get_send("send-never-seen"))

    def test_unknown_send_returns_none_when_core_has_no_record(self) -> None:
        class _NotFound(CoreApiError):
            pass

        original = self.service.core.send_status
        self.service.core.send_status = lambda send_id: (_ for _ in ()).throw(
            CoreApiError(404, "send_not_found", "Unknown send_id", {})
        )
        try:
            self.assertIsNone(self.service.send_status("send-missing"))
        finally:
            self.service.core.send_status = original

    # -- readiness contract ----------------------------------------------

    def test_status_exposes_authoritative_send_readiness(self) -> None:
        status = self.service.status()
        account = status["accounts"][0]
        self.assertTrue(account["send_ready"])
        self.assertEqual(account["send_blocked_reason"], "")

        self.state.send_ready = False
        self.state.blocked_reason = "wechat_ready_unknown"
        self.state.blocked_message = "正在确认微信登录状态，请稍候。"
        account = self.service.status()["accounts"][0]
        self.assertFalse(account["send_ready"])
        self.assertEqual(account["send_blocked_message"], "正在确认微信登录状态，请稍候。")


class ConsoleSendHttpTest(unittest.TestCase):
    """End-to-end HTTP contract for the not-ready send gate."""

    @classmethod
    def setUpClass(cls) -> None:
        state = _FakeCoreState()
        handler = type("_BoundFakeCoreHandler", (_FakeCoreHandler,), {"state": state})
        cls.state = state
        cls.core_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.core_url = f"http://127.0.0.1:{cls.core_server.server_port}"
        cls.core_thread = threading.Thread(target=cls.core_server.serve_forever, daemon=True)
        cls.core_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.core_server.shutdown()
        cls.core_server.server_close()
        cls.core_thread.join(timeout=2)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = ConsoleService(
            core_url=self.core_url,
            db_path=root / "console.sqlite",
            archive_dir=root / "saved-attachments",
        )
        self.state.send_http_status = 202
        self.state.send_http_body = {
            "send_id": "send-new",
            "account_id": "arasial",
            "chat_id": "chat-1",
            "kind": "text",
            "status": "accepted",
            "accepted_at": "2026-09-19T13:00:00Z",
        }
        self.console = create_server("127.0.0.1", 0, self.service)
        self.console_url = f"http://127.0.0.1:{self.console.server_port}"
        self.console_thread = threading.Thread(target=self.console.serve_forever, daemon=True)
        self.console_thread.start()

    def tearDown(self) -> None:
        self.console.shutdown()
        self.console.server_close()
        self.console_thread.join(timeout=2)
        self.service.stop()
        self.temp.cleanup()

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.console_url + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Idempotency-Key": uuid.uuid4().hex},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_not_ready_send_is_rejected_with_a_readable_reason(self) -> None:
        self.state.send_http_status = 409
        self.state.send_http_body = {
            "error": {
                "code": "wechat_not_ready",
                "message": "微信正在完成登录，请稍候。",
                "details": {
                    "account_id": "arasial",
                    "reason": "wechat_ready_unknown",
                    "user_message": "微信正在完成登录，请稍候。",
                    "retryable": True,
                },
            }
        }
        status, payload = self._post(
            "/api/send/text", {"account_id": "arasial", "chat_id": "chat-1", "text": "hi"}
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "wechat_not_ready")
        self.assertEqual(payload["error"]["message"], "微信正在完成登录，请稍候。")
        # Nothing may be parked in the local mirror for a rejected send.
        self.assertIsNone(self.service.store.get_send("send-new"))

    def test_accepted_send_is_mirrored_for_convergence(self) -> None:
        status, payload = self._post(
            "/api/send/text", {"account_id": "arasial", "chat_id": "chat-1", "text": "hi"}
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["send_id"], "send-new")
        self.assertEqual(self.service.store.get_send("send-new")["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
