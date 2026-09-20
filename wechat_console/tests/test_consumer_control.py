"""Console consumer control (P1-1 / P1-2 / P1-3 / P1-4).

The Console is a presentation/action surface only:

    Console -> Core consumer API -> Runtime control socket -> ConsumerControl -> Docker

These tests pin the properties that keep that boundary honest:

* consumer state comes exclusively from the Runtime snapshot exposed by Core;
* the Console never probes a consumer port to decide whether it is running, so
  a stopped-but-still-answering container cannot be reported as running;
* the Agent's functional API is only called while the Runtime reports the
  consumer running;
* an invalid mode or an unknown consumer is rejected before Core is called.
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


def _consumer(consumer: str, *, running: bool, configured: bool = True, state: str = "") -> dict:
    resolved = state or ("running" if running else ("stopped" if configured else "not_configured"))
    return {
        "consumer": consumer,
        "display_name": consumer.upper(),
        "summary": "",
        "container_name": f"wechat-hub-{consumer}",
        "container_id": f"id-{consumer}",
        "image": f"ghcr.io/onestao/wechat-hub-{consumer}@sha256:" + "a" * 64,
        "current_image": "",
        "image_present": True,
        "provisioned": True,
        "configured": configured,
        "configuration_detail": "" if configured else "尚未配置 Telegram",
        "state": resolved,
        "running": running,
        "started_at": "",
        "finished_at": "",
        "restart_count": 0,
        "exit_code": 0,
        "can_start": configured,
        "blocked_reason": "" if configured else "尚未配置 Telegram",
        "last_error": "",
    }


class _FakeCoreState:
    def __init__(self) -> None:
        self.running = ""
        self.efb_configured = False
        self.calls: list[tuple[str, dict]] = []

    def snapshot(self) -> dict:
        return {
            "mode": self.running or "disabled",
            "desired_mode": self.running or "disabled",
            "modes": ["disabled", "efb", "agent"],
            "mutual_exclusion": True,
            "consumers": {
                "efb": _consumer("efb", running=self.running == "efb", configured=self.efb_configured),
                "agent": _consumer("agent", running=self.running == "agent"),
            },
            "runtime": {"available": True},
        }


class _FakeCoreHandler(BaseHTTPRequestHandler):
    state: _FakeCoreState

    def log_message(self, *args) -> None:
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        self.state.calls.append(("GET", {"path": path}))
        if path == "/health":
            self._json(200, {"ok": True, "service": "wechat-core", "contract_version": 1, "accounts": 1})
            return
        if path == "/v1/accounts":
            self._json(200, {"accounts": []})
            return
        if path == "/v1/consumers":
            self._json(200, self.state.snapshot())
            return
        if path == "/v1/install/status":
            snapshot = self.state.snapshot()
            consumers = snapshot["consumers"]
            self._json(
                200,
                {
                    "core": {"state": "ready", "accounts": 0},
                    "runtime": {"state": "ready", "error": ""},
                    "wechat": {"state": "not_configured", "accounts": 0},
                    "efb": {
                        "consumer": "efb",
                        "display_name": "EFB (Telegram)",
                        "state": "not_configured" if not self.state.efb_configured else consumers["efb"]["state"],
                        "configured": self.state.efb_configured,
                        "running": consumers["efb"]["running"],
                        "can_start": self.state.efb_configured,
                        "blocked_reason": "" if self.state.efb_configured else "尚未配置 Telegram",
                        "last_error": "",
                    },
                    "agent": {
                        "consumer": "agent",
                        "display_name": "Agent",
                        "state": consumers["agent"]["state"],
                        "configured": True,
                        "running": consumers["agent"]["running"],
                        "can_start": True,
                        "blocked_reason": "",
                        "last_error": "",
                    },
                    "consumer_mode": snapshot["mode"],
                    "desired_mode": snapshot["desired_mode"],
                    "mutual_exclusion": True,
                },
            )
            return
        if path == "/v1/runtime/accounts":
            self._json(200, {"accounts": [], "registry_reload": {"ok": True}})
            return
        if path == "/v1/events/poll":
            self._json(
                200,
                {
                    "events": [],
                    "next_cursor": "0",
                    "has_more": False,
                    "stream_head_cursor": 0,
                    "retention_floor_cursor": 0,
                },
            )
            return
        self._json(404, {"error": {"code": "not_found", "message": path}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        self.state.calls.append(("POST", {"path": path, "payload": payload}))
        if path == "/v1/consumers/mode":
            mode = str(payload.get("mode") or "disabled")
            self.state.running = "" if mode == "disabled" else mode
            self._json(200, self.state.snapshot())
            return
        if path.startswith("/v1/consumers/"):
            parts = path[len("/v1/consumers/") :].split("/")
            if len(parts) == 2:
                consumer, operation = parts
                if operation == "start":
                    self.state.running = consumer
                elif self.state.running == consumer:
                    self.state.running = ""
                self._json(200, self.state.snapshot())
                return
        if path == "/v1/events/ack":
            self._json(200, {"acked_count": 0})
            return
        if path == "/v1/events/checkpoint":
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": {"code": "not_found", "message": path}})


class _FakeAgentState:
    def __init__(self) -> None:
        self.requests: list[str] = []


class _FakeAgentHandler(BaseHTTPRequestHandler):
    state: _FakeAgentState

    def log_message(self, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        self.state.requests.append(path)
        body = json.dumps({"status": "ok", "monitors": [], "schedules": [], "templates": []}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ConsumerControlConsoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.core_state = _FakeCoreState()
        core_handler = type("_BoundFakeCoreHandler", (_FakeCoreHandler,), {"state": cls.core_state})
        cls.core_server = ThreadingHTTPServer(("127.0.0.1", 0), core_handler)
        cls.core_url = f"http://127.0.0.1:{cls.core_server.server_port}"
        cls.core_thread = threading.Thread(target=cls.core_server.serve_forever, daemon=True)
        cls.core_thread.start()

        cls.agent_state = _FakeAgentState()
        agent_handler = type("_BoundFakeAgentHandler", (_FakeAgentHandler,), {"state": cls.agent_state})
        cls.agent_server = ThreadingHTTPServer(("127.0.0.1", 0), agent_handler)
        cls.agent_url = f"http://127.0.0.1:{cls.agent_server.server_port}"
        cls.agent_thread = threading.Thread(target=cls.agent_server.serve_forever, daemon=True)
        cls.agent_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        for server, thread in (
            (cls.core_server, cls.core_thread),
            (cls.agent_server, cls.agent_thread),
        ):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def setUp(self) -> None:
        self.core_state.running = ""
        self.core_state.efb_configured = False
        self.core_state.calls.clear()
        self.agent_state.requests.clear()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = ConsoleService(
            core_url=self.core_url,
            db_path=root / "console.sqlite",
            archive_dir=root / "saved-attachments",
            agent_url=self.agent_url,
        )
        self.console = create_server("127.0.0.1", 0, self.service)
        self.console_url = f"http://127.0.0.1:{self.console.server_port}"
        self.thread = threading.Thread(target=self.console.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.console.shutdown()
        self.console.server_close()
        self.thread.join(timeout=2)
        self.service.stop()
        self.temp.cleanup()

    def request(self, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        request = urllib.request.Request(self.console_url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, (json.loads(raw) if raw else {})

    # -- state source ------------------------------------------------------

    def test_status_exposes_install_state_from_the_runtime(self) -> None:
        status, payload = self.request("/api/status")
        self.assertEqual(status, 200)
        install = payload["install"]
        self.assertEqual(install["wechat"]["state"], "not_configured")
        self.assertEqual(install["efb"]["state"], "not_configured")
        self.assertEqual(install["agent"]["state"], "stopped")
        self.assertEqual(install["consumer_mode"], "disabled")
        self.assertTrue(install["mutual_exclusion"])
        # The retired liveness probe is gone.
        self.assertNotIn("integrations", payload)

    def test_status_never_probes_the_agent_port(self) -> None:
        """A reachable port is not a lifecycle state."""

        self.request("/api/status")
        self.assertEqual(self.agent_state.requests, [])

    def test_install_status_endpoint_passes_through(self) -> None:
        status, payload = self.request("/api/install/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["agent"]["state"], "stopped")

    def test_consumers_endpoint_passes_through(self) -> None:
        status, payload = self.request("/api/consumers")
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "disabled")
        self.assertEqual(set(payload["consumers"]), {"efb", "agent"})

    # -- actions -----------------------------------------------------------

    def test_start_and_stop_agent_go_through_core(self) -> None:
        status, payload = self.request("/api/consumers/agent/start", method="POST", payload={})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["consumers"]["agent"]["running"])
        self.assertIn(("POST", {"path": "/v1/consumers/agent/start", "payload": {}}), self.core_state.calls)

        status, payload = self.request("/api/consumers/agent/stop", method="POST", payload={})
        self.assertEqual(status, 200)
        self.assertFalse(payload["consumers"]["agent"]["running"])

    def test_set_mode_goes_through_core(self) -> None:
        status, payload = self.request("/api/consumers/mode", method="POST", payload={"mode": "agent"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["mode"], "agent")

    def test_invalid_mode_is_rejected_before_core(self) -> None:
        self.core_state.calls.clear()
        status, payload = self.request("/api/consumers/mode", method="POST", payload={"mode": "bogus"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertEqual([call for call in self.core_state.calls if call[0] == "POST"], [])

    def test_unknown_consumer_is_rejected_before_core(self) -> None:
        self.core_state.calls.clear()
        status, payload = self.request("/api/consumers/bogus/start", method="POST", payload={})
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")
        self.assertEqual([call for call in self.core_state.calls if call[0] == "POST"], [])

    # -- agent functional API gating ---------------------------------------

    def test_agent_api_is_refused_while_the_consumer_is_stopped(self) -> None:
        status, payload = self.request("/api/agent/status")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "agent_not_running")
        self.assertEqual(payload["error"]["details"]["state"], "stopped")
        # The Console must not have called the Agent's functional API at all.
        self.assertEqual(self.agent_state.requests, [])

    def test_agent_api_is_reachable_while_the_consumer_is_running(self) -> None:
        self.core_state.running = "agent"
        status, payload = self.request("/api/agent/status")
        self.assertEqual(status, 200, payload)
        self.assertTrue(self.agent_state.requests)


if __name__ == "__main__":
    unittest.main()
