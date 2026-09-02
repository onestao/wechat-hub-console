from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


SUPPORTED_CONTRACT_VERSION = 1


@dataclass(slots=True)
class CoreApiError(RuntimeError):
    status: int
    code: str
    message: str
    details: dict[str, Any]

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class CoreClient:
    """Small client for the frozen Core Interface Contract V1.

    It deliberately has no knowledge of Core's implementation files or SQLite
    layout.  The Console's only required dependency is this HTTP boundary.
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            clean = {key: value for key, value in query.items() if value not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        return url

    def _json_request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        body = None
        request_headers = {"Accept": "application/json"}
        request_headers.update(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._url(path, query),
            method=method,
            data=body,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = {}
            error = parsed.get("error") if isinstance(parsed, dict) else {}
            if not isinstance(error, dict):
                error = {}
            raise CoreApiError(
                status=exc.code,
                code=str(error.get("code") or "core_http_error"),
                message=str(error.get("message") or exc.reason or "Core request failed"),
                details=error.get("details") if isinstance(error.get("details"), dict) else {},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CoreApiError(503, "core_unavailable", str(exc), {}) from exc

        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CoreApiError(502, "invalid_core_json", "Core returned invalid JSON", {}) from exc
        if not isinstance(parsed, dict):
            raise CoreApiError(502, "invalid_core_json", "Core JSON must be an object", {})
        return parsed

    def health(self, *, require_supported: bool = True) -> dict[str, Any]:
        payload = self._json_request("/health")
        version = payload.get("contract_version")
        if require_supported and version != SUPPORTED_CONTRACT_VERSION:
            raise CoreApiError(
                502,
                "unsupported_contract_version",
                f"Console supports Core contract {SUPPORTED_CONTRACT_VERSION}; Core advertises {version!r}",
                {"supported": SUPPORTED_CONTRACT_VERSION, "advertised": version},
            )
        return payload

    def accounts(self) -> list[dict[str, Any]]:
        payload = self._json_request("/v1/accounts")
        rows = payload.get("accounts") or []
        return [row for row in rows if isinstance(row, dict)]

    def runtime_accounts(self) -> dict[str, Any]:
        return self._json_request("/v1/runtime/accounts", timeout=max(self.timeout, 20.0))

    def create_runtime_account(
        self,
        *,
        account_id: str,
        display_name: str = "",
        display: str = "",
        runtime_provider: str = "agent_wechat",
        autostart: bool = True,
        start: bool = True,
    ) -> dict[str, Any]:
        return self._json_request(
            "/v1/runtime/accounts",
            method="POST",
            payload={
                "account_id": account_id,
                "display_name": display_name,
                "display": display,
                "runtime_provider": runtime_provider,
                "autostart": bool(autostart),
                "start": bool(start),
            },
            timeout=max(self.timeout, 20.0),
        )

    def runtime_account_action(self, account_id: str, action: str) -> dict[str, Any]:
        if action not in {"start", "stop", "restart"}:
            raise ValueError("action must be start, stop, or restart")
        account = urllib.parse.quote(account_id, safe="")
        return self._json_request(
            f"/v1/runtime/accounts/{account}/{action}",
            method="POST",
            payload={},
            timeout=max(self.timeout, 20.0),
        )

    def delete_runtime_account(self, account_id: str, *, purge_data: bool = False) -> dict[str, Any]:
        account = urllib.parse.quote(account_id, safe="")
        query = "?purge_data=1" if purge_data else ""
        return self._json_request(
            f"/v1/runtime/accounts/{account}{query}",
            method="DELETE",
            timeout=max(self.timeout, 20.0),
        )

    def runtime_desktop(self, account_id: str) -> dict[str, Any]:
        account = urllib.parse.quote(account_id, safe="")
        return self._json_request(f"/v1/runtime/accounts/{account}/desktop")

    def runtime_login(self, account_id: str) -> dict[str, Any]:
        account = urllib.parse.quote(account_id, safe="")
        return self._json_request(f"/v1/runtime/accounts/{account}/login")

    def runtime_login_start(self, account_id: str) -> dict[str, Any]:
        account = urllib.parse.quote(account_id, safe="")
        return self._json_request(
            f"/v1/runtime/accounts/{account}/login",
            method="POST",
            payload={},
            timeout=max(self.timeout, 20.0),
        )

    def runtime_login_snapshot(self, account_id: str) -> tuple[bytes, str]:
        account = urllib.parse.quote(account_id, safe="")
        request = urllib.request.Request(
            self._url(f"/v1/runtime/accounts/{account}/login/snapshot"),
            headers={"Accept": "image/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout, 12.0)) as response:
                return response.read(), response.headers.get_content_type() or "image/png"
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = {}
            error = parsed.get("error") if isinstance(parsed, dict) else {}
            if not isinstance(error, dict):
                error = {}
            raise CoreApiError(
                exc.code,
                str(error.get("code") or "core_http_error"),
                str(error.get("message") or exc.reason or "Core login snapshot request failed"),
                error.get("details") if isinstance(error.get("details"), dict) else {},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CoreApiError(503, "core_unavailable", str(exc), {}) from exc

    def chats(self, account_id: str, *, query: str = "", limit: int = 200) -> dict[str, Any]:
        account = urllib.parse.quote(account_id, safe="")
        return self._json_request(
            f"/v1/accounts/{account}/chats",
            query={"query": query, "limit": max(1, min(int(limit), 200))},
        )

    def poll_events(
        self,
        *,
        after: str = "",
        limit: int = 200,
        account_id: str = "",
        consumer_id: str = "wechat-console",
        timeout: int = 0,
    ) -> dict[str, Any]:
        return self._json_request(
            "/v1/events/poll",
            query={
                "after": after,
                "limit": max(1, min(int(limit), 200)),
                "account_id": account_id,
                "consumer_id": consumer_id,
                "timeout": max(0, min(int(timeout), 30)),
            },
        )

    def ack_events(self, consumer_id: str, event_ids: list[str]) -> dict[str, Any]:
        return self._json_request(
            "/v1/events/ack",
            method="POST",
            payload={"consumer_id": consumer_id, "event_ids": event_ids},
        )

    def media(self, account_id: str, media_id: str) -> tuple[bytes, str, str]:
        media = urllib.parse.quote(media_id, safe="")
        request = urllib.request.Request(
            self._url(f"/v1/media/{media}", {"account_id": account_id}),
            headers={"Accept": "*/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                mime_type = response.headers.get_content_type() or "application/octet-stream"
                disposition = response.headers.get("Content-Disposition", "")
                filename = _filename_from_disposition(disposition) or media_id
                return body, mime_type, filename
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = {}
            error = parsed.get("error") if isinstance(parsed, dict) else {}
            if not isinstance(error, dict):
                error = {}
            raise CoreApiError(
                exc.code,
                str(error.get("code") or "core_http_error"),
                str(error.get("message") or exc.reason or "Core media request failed"),
                error.get("details") if isinstance(error.get("details"), dict) else {},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CoreApiError(503, "core_unavailable", str(exc), {}) from exc

    def send_text(
        self,
        *,
        account_id: str,
        chat_id: str,
        text: str,
        target_message_id: str = "",
        mention_member_ids: list[str] | None = None,
        client_request_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "account_id": account_id,
            "chat_id": chat_id,
            "text": text,
        }
        if target_message_id:
            payload["target_message_id"] = target_message_id
        if mention_member_ids:
            payload["mention_member_ids"] = mention_member_ids
        if client_request_id:
            payload["client_request_id"] = client_request_id
        return self._json_request(
            "/v1/send/text",
            method="POST",
            payload=payload,
            headers={"Idempotency-Key": idempotency_key} if idempotency_key else {},
        )

    def send_media(self, kind: str, payload: dict[str, Any], *, idempotency_key: str = "") -> dict[str, Any]:
        if kind not in {"image", "file"}:
            raise ValueError("kind must be image or file")
        return self._json_request(
            f"/v1/send/{kind}",
            method="POST",
            payload=payload,
            headers={"Idempotency-Key": idempotency_key} if idempotency_key else {},
        )


def _filename_from_disposition(value: str) -> str:
    if not value:
        return ""
    for part in value.split(";"):
        key, sep, raw = part.strip().partition("=")
        if sep and key.lower() == "filename":
            return raw.strip().strip('"')
    return ""
