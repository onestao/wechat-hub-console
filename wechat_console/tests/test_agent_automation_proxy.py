"""Console proxy tests for the optional Agent automation surface.

Covers taskbook F3/F9 at the Console boundary:
- Console CRUD calls really reach the Agent service (list/create/delete/runs);
- when the Agent is not configured the Console reports a structured honest
  error instead of pretending automation exists;
- the browser bundle is wired to the agent routes and shows accurate states.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from wechat_console.app import ConsoleService, create_server


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


class MockAgentState:
    """In-memory stand-in for the Agent service REST contract."""

    def __init__(self) -> None:
        self.monitors: dict[str, dict] = {}
        self.schedules: dict[str, dict] = {}
        self.templates: dict[str, dict] = {}
        self.runs: dict[str, list[dict]] = {}

    def upsert(self, table: str, payload: dict) -> dict:
        key = {"monitors": "monitor_id", "schedules": "schedule_id", "templates": "template_id"}[table]
        item = dict(payload)
        item.setdefault(key, f"{table[:-1]}-generated")
        item.setdefault("enabled", True)
        getattr(self, table)[item[key]] = item
        return item


class MockAgentHandler(BaseHTTPRequestHandler):
    server_version = "MockAgent/1"
    state: MockAgentState

    def log_message(self, fmt: str, *args) -> None:
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _table(self, path: str) -> tuple[str, str] | None:
        parsed = urlparse(path)
        clean = parsed.path.rstrip("/")
        for table in ("monitors", "schedules", "templates"):
            prefix = f"/api/{table}/"
            if clean == f"/api/{table}":
                return table, ""
            if clean.startswith(prefix):
                suffix = unquote(clean[len(prefix) :])
                if suffix.endswith("/runs"):
                    return f"{table}:runs", suffix[: -len("/runs")]
                return table, suffix
        return None

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path.rstrip("/") == "/api/status":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "wechat-agent",
                    "counts": {"monitors": len(self.state.monitors)},
                    "workers": {"running": True, "last_poll": {}, "last_scheduler": {}},
                    "core": {"ok": True},
                },
            )
            return
        resolved = self._table(self.path)
        if not resolved:
            self._json(404, {"ok": False, "error": "not found"})
            return
        table, resource_id = resolved
        if table.endswith(":runs"):
            store = table.split(":")[0]
            if resource_id not in getattr(self.state, store):
                self._json(404, {"ok": False, "error": "not found"})
                return
            self._json(200, {"runs": self.state.runs.get(resource_id, [])})
            return
        rows = list(getattr(self.state, table).values())
        self._json(200, {table: rows})

    def do_POST(self) -> None:  # noqa: N802
        resolved = self._table(self.path)
        if not resolved:
            self._json(404, {"ok": False, "error": "not found"})
            return
        table, resource_id = resolved
        if resource_id:
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if table == "monitors" and not str(payload.get("account_id") or "").strip():
            # Mirror the Agent's legacy error shape for validation failures.
            self._json(400, {"ok": False, "error": "monitor account_id is required"})
            return
        item = self.state.upsert(table, payload)
        key = {"monitors": "monitor_id", "schedules": "schedule_id", "templates": "template_id"}[table]
        self.state.runs.setdefault(item[key], []).append(
            {
                "status": "success",
                "error": "",
                "created_at": "2026-09-06T08:00:00+00:00",
                "result": {"identity": {"account_id": payload.get("account_id") or ""}},
            }
        )
        self._json(200, item)

    def do_DELETE(self) -> None:  # noqa: N802
        resolved = self._table(self.path)
        if not resolved or not resolved[1]:
            self._json(404, {"ok": False, "error": "not found"})
            return
        table, resource_id = resolved
        store = getattr(self.state, table)
        if resource_id not in store:
            self._json(404, {"ok": False, "error": "not found"})
            return
        del store[resource_id]
        self.state.runs.pop(resource_id, None)
        self._json(200, {"ok": True})


class _FakeConsumerCoreHandler(BaseHTTPRequestHandler):
    """Minimal Core that only answers the consumer snapshot.

    The Console no longer treats a reachable Agent port as a lifecycle state, so
    these proxy tests must supply the Runtime-derived state through Core.
    """

    agent_running: bool = True

    def log_message(self, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/v1/consumers":
            self._json(404, {"error": {"code": "not_found", "message": path}})
            return
        self._json(
            200,
            {
                "mode": "agent" if self.agent_running else "disabled",
                "desired_mode": "agent" if self.agent_running else "disabled",
                "modes": ["disabled", "efb", "agent"],
                "mutual_exclusion": True,
                "consumers": {
                    "efb": {"consumer": "efb", "state": "stopped", "running": False, "can_start": False},
                    "agent": {
                        "consumer": "agent",
                        "state": "running" if self.agent_running else "stopped",
                        "running": self.agent_running,
                        "can_start": True,
                        "blocked_reason": "",
                        "last_error": "",
                    },
                },
                "runtime": {"available": True},
            },
        )

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AgentProxyIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agent_state = MockAgentState()
        cls.agent_server = ThreadingHTTPServer(("127.0.0.1", 0), type("Bound", (MockAgentHandler,), {"state": cls.agent_state}))
        cls.agent_url = f"http://127.0.0.1:{cls.agent_server.server_port}"
        cls.agent_thread = threading.Thread(target=cls.agent_server.serve_forever, daemon=True)
        cls.agent_thread.start()

        cls.consumer_core = ThreadingHTTPServer(
            ("127.0.0.1", 0), type("BoundConsumerCore", (_FakeConsumerCoreHandler,), {"agent_running": True})
        )
        cls.core_url = f"http://127.0.0.1:{cls.consumer_core.server_port}"
        cls.core_thread = threading.Thread(target=cls.consumer_core.serve_forever, daemon=True)
        cls.core_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        for server, thread in ((cls.agent_server, cls.agent_thread), (cls.consumer_core, cls.core_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def setUp(self) -> None:
        self.temp = __import__("tempfile").TemporaryDirectory()
        self.service = ConsoleService(
            core_url=self.core_url,
            db_path=Path(self.temp.name) / "console.sqlite",
            archive_dir=Path(self.temp.name) / "saved-attachments",
            agent_url=self.agent_url,
        )

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    def test_monitor_crud_and_runs_reach_agent(self) -> None:
        created = self.service.agent.upsert_monitor(
            {
                "monitor_id": "mon-1",
                "name": "reply rule",
                "account_id": "account-alpha",
                "expected_wechat_identity_uuid": "identity-alpha-uuid",
                "action": "send_text",
                "action_config": {"text": "收到"},
                "enabled": True,
            }
        )
        self.assertEqual(created["monitor_id"], "mon-1")
        self.assertIn("mon-1", self.agent_state.monitors)

        self.service.agent.delete_monitor("mon-1")
        self.assertNotIn("mon-1", self.agent_state.monitors)

    def test_schedule_and_template_crud_reach_agent(self) -> None:
        created = self.service.agent.upsert_schedule(
            {
                "schedule_id": "sch-1",
                "name": "hourly",
                "task_type": "send_text",
                "account_id": "account-alpha",
                "chat_id": "chat-1",
                "expected_wechat_identity_uuid": "identity-alpha-uuid",
            }
        )
        self.assertEqual(created["schedule_id"], "sch-1")
        self.service.agent.upsert_template({"template_id": "tpl-1", "name": "t", "body": "b"})
        self.assertEqual(self.service.agent.templates()[0]["template_id"], "tpl-1")
        self.service.agent.delete_schedule("sch-1")
        self.service.agent.delete_template("tpl-1")
        self.assertEqual(self.agent_state.schedules, {})
        self.assertEqual(self.agent_state.templates, {})

    def test_console_http_proxy_surfaces_agent_data(self) -> None:
        self.agent_state.upsert("monitors", {"monitor_id": "mon-http", "name": "http rule", "account_id": "account-alpha"})
        self.agent_state.runs["mon-http"] = [
            {
                "status": "success",
                "error": "",
                "created_at": "2026-09-06T08:00:00+00:00",
                "result": {"identity": {"account_id": "account-alpha"}},
            }
        ]
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            status, payload = self.request(base + "/api/agent/monitors")
            self.assertEqual(status, 200)
            self.assertEqual(payload["monitors"][0]["monitor_id"], "mon-http")

            status, payload = self.request(base + "/api/agent/monitors/mon-http/runs")
            self.assertEqual(status, 200)
            self.assertEqual(payload["runs"][0]["status"], "success")
            self.assertEqual(payload["runs"][0]["result"]["identity"]["account_id"], "account-alpha")

            status, payload = self.request(
                base + "/api/agent/monitors",
                method="POST",
                payload={"monitor_id": "mon-http2", "name": "x", "account_id": "account-beta"},
            )
            self.assertEqual(status, 200)
            self.assertIn("mon-http2", self.agent_state.monitors)

            status, payload = self.request(base + "/api/agent/monitors", method="POST", payload={"name": "no scope"})
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"]["code"], "agent_error")
            self.assertIn("account_id", payload["error"]["message"])

            status, payload = self.request(base + "/api/agent/monitors/mon-http2", method="DELETE")
            self.assertEqual(status, 200)
            self.assertNotIn("mon-http2", self.agent_state.monitors)

            status, payload = self.request(base + "/api/agent/status")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["agent"]["service"], "wechat-agent")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_stopped_agent_consumer_reports_structured_honest_error(self) -> None:
        """The Agent functional API is refused unless the Runtime reports it running."""

        self.consumer_core.RequestHandlerClass.agent_running = False
        try:
            server = create_server("127.0.0.1", 0, self.service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                for path in ("/api/agent/status", "/api/agent/monitors", "/api/agent/schedules"):
                    status, payload = self.request(base + path)
                    self.assertEqual(status, 409, path)
                    self.assertEqual(payload["error"]["code"], "agent_not_running", path)
                    self.assertEqual(payload["error"]["details"]["state"], "stopped", path)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        finally:
            self.consumer_core.RequestHandlerClass.agent_running = True

    def test_unreachable_core_reports_consumer_state_unavailable(self) -> None:
        """No Runtime snapshot means no answer, never a guess."""

        self.service = ConsoleService(
            core_url="http://127.0.0.1:1",
            db_path=Path(self.temp.name) / "console2.sqlite",
            archive_dir=Path(self.temp.name) / "saved-attachments2",
            agent_url=self.agent_url,
        )
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for path in ("/api/agent/status", "/api/agent/monitors", "/api/agent/schedules"):
                status, payload = self.request(base + path)
                self.assertEqual(status, 503, path)
                self.assertEqual(payload["error"]["code"], "consumer_state_unavailable", path)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_console_browser_bundle_is_wired_to_agent_surface(self) -> None:
        api_js = (STATIC_DIR / "js" / "api.js").read_text(encoding="utf-8")
        for needle in (
            "agentStatus:",
            "agentMonitors:",
            "saveAgentMonitor:",
            "deleteAgentMonitor:",
            "agentMonitorRuns:",
            "agentSchedules:",
            "saveAgentSchedule:",
            "agentScheduleRuns:",
            "agentTemplates:",
            "deleteAgentTemplate:",
        ):
            self.assertIn(needle, api_js)

        automation_js = (STATIC_DIR / "js" / "views" / "automation.js").read_text(encoding="utf-8")
        # Honest-state strings required by the taskbook.
        self.assertIn("当前版本尚未提供此自动化能力", automation_js)
        self.assertIn("当前部署未启用自动化服务", automation_js)
        self.assertIn("无法连接自动化服务", automation_js)
        # The old marketing copy must be gone.
        self.assertNotIn("启用后可以做什么", automation_js)
        self.assertNotIn("启动 WeChat Agent 后，可以创建自动回复", automation_js)
        # Real CRUD wiring must be present.
        for needle in (
            "saveAgentMonitor(",
            "saveAgentSchedule(",
            "saveAgentTemplate(",
            "confirmAction(",
            "expected_wechat_identity_uuid",
        ):
            self.assertIn(needle, automation_js)

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
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


if __name__ == "__main__":
    unittest.main()
