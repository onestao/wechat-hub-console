"""HTTP client for the optional WeChat Agent automation service.

The Console treats Agent as an optional integration: every call either
returns live data from the Agent service or raises a structured
:class:`AgentApiError` so the UI can show an accurate "not available" state
instead of pretending automation exists.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AgentApiError(RuntimeError):
    status: int
    code: str
    message: str
    details: dict[str, Any]

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class AgentClient:
    """Small stdlib client for the WeChat Agent REST surface."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = max(1.0, float(timeout))

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise AgentApiError(503, "agent_not_configured", "WECHAT_AGENT_URL is not configured", {})
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._url(path),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            raise AgentApiError(exc.code, *_parse_agent_error(raw)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AgentApiError(503, "agent_unavailable", str(exc), {}) from exc
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentApiError(502, "invalid_agent_json", "Agent returned invalid JSON", {}) from exc
        if not isinstance(parsed, dict):
            raise AgentApiError(502, "invalid_agent_json", "Agent JSON must be an object", {})
        return parsed

    # -- read surface -------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return self._request("/api/status")

    def monitors(self) -> list[dict[str, Any]]:
        payload = self._request("/api/monitors")
        rows = payload.get("monitors") or []
        return [row for row in rows if isinstance(row, dict)]

    def monitor_runs(self, monitor_id: str) -> dict[str, Any]:
        return self._request(f"/api/monitors/{urllib.parse.quote(monitor_id, safe='')}/runs")

    def schedules(self) -> list[dict[str, Any]]:
        payload = self._request("/api/schedules")
        rows = payload.get("schedules") or []
        return [row for row in rows if isinstance(row, dict)]

    def schedule_runs(self, schedule_id: str) -> dict[str, Any]:
        return self._request(f"/api/schedules/{urllib.parse.quote(schedule_id, safe='')}/runs")

    def templates(self) -> list[dict[str, Any]]:
        payload = self._request("/api/templates")
        rows = payload.get("templates") or []
        return [row for row in rows if isinstance(row, dict)]

    # -- write surface ------------------------------------------------------

    def upsert_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/api/monitors", method="POST", payload=payload)

    def delete_monitor(self, monitor_id: str) -> dict[str, Any]:
        return self._request(
            f"/api/monitors/{urllib.parse.quote(monitor_id, safe='')}", method="DELETE"
        )

    def upsert_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/api/schedules", method="POST", payload=payload)

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        return self._request(
            f"/api/schedules/{urllib.parse.quote(schedule_id, safe='')}", method="DELETE"
        )

    def upsert_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/api/templates", method="POST", payload=payload)

    def delete_template(self, template_id: str) -> dict[str, Any]:
        return self._request(
            f"/api/templates/{urllib.parse.quote(template_id, safe='')}", method="DELETE"
        )


def _parse_agent_error(raw: bytes) -> tuple[str, str, dict[str, Any]]:
    """Agent replies use Core's {error:{code,message}} shape for some paths and
    the legacy {ok:false,error:"..."} shape for others; accept both."""
    try:
        parsed = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {}
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):
        return (
            str(error.get("code") or "agent_error"),
            str(error.get("message") or "Agent request failed"),
            error.get("details") if isinstance(error.get("details"), dict) else {},
        )
    if isinstance(error, str) and error.strip():
        return "agent_error", error, {}
    return "agent_error", "Agent request failed", {}
