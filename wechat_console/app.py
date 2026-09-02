#!/usr/bin/env python3
"""Decoupled human-operations console for WeChat Hub.

This service is derived from the stdlib HTTP-server architecture used by the
upstream linux-wechat-agent Console, but its WeChat boundary is Core HTTP V1.
It never opens Core SQLite files and it can start with EFB and Agent absent.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

try:
    from .core_client import CoreApiError, CoreClient, SUPPORTED_CONTRACT_VERSION
    from .store import ConsoleStore, utc_now
except ImportError:  # pragma: no cover - supports `python wechat_console/app.py`
    from core_client import CoreApiError, CoreClient, SUPPORTED_CONTRACT_VERSION
    from store import ConsoleStore, utc_now


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
_runtime_dir = os.environ.get("WECHAT_CONSOLE_RUNTIME_DIR", "").strip()
DEFAULT_RUNTIME_DIR = Path(_runtime_dir) if _runtime_dir else PACKAGE_DIR.parent / "runtime" / "wechat-console"
DEFAULT_DB = DEFAULT_RUNTIME_DIR / "console.sqlite"
DEFAULT_ARCHIVE_DIR = DEFAULT_RUNTIME_DIR / "saved-attachments"


class ConsoleService:
    def __init__(
        self,
        *,
        core_url: str,
        db_path: str | Path,
        archive_dir: str | Path,
        agent_url: str = "",
        efb_url: str = "",
        desktop_url: str = "",
        consumer_id: str = "wechat-console",
        core_timeout: float = 5.0,
    ) -> None:
        self.core = CoreClient(core_url, timeout=core_timeout)
        self.store = ConsoleStore(db_path, archive_dir)
        self.agent_url = agent_url.rstrip("/")
        self.efb_url = efb_url.rstrip("/")
        self.desktop_url = desktop_url.rstrip("/")
        self.consumer_id = consumer_id
        self._stop = threading.Event()
        self._sync_thread: threading.Thread | None = None
        self._sync_lock = threading.Lock()
        self.last_sync: dict[str, Any] = {
            "ok": None,
            "at": "",
            "events": 0,
            "cursor": self.store.cursor(),
            "error": "",
        }

    @property
    def core_url(self) -> str:
        return self.core.base_url

    def start_background_sync(self, interval_seconds: float = 2.0) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            return
        interval = max(0.5, float(interval_seconds))

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.sync_events_once(max_pages=5)
                except Exception as exc:  # defensive: loop must not kill Console
                    self.store.log("error", "core-sync", "Core event sync failed", {"error": str(exc)})
                self._stop.wait(interval)

        self._sync_thread = threading.Thread(target=loop, daemon=True, name="console-core-events")
        self._sync_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2)

    def sync_events_once(self, *, max_pages: int = 3) -> dict[str, Any]:
        if not self._sync_lock.acquire(blocking=False):
            return {"ok": True, "skipped": True, "reason": "sync_already_running", **self.last_sync}
        try:
            self.core.health(require_supported=True)
            total = 0
            acked = 0
            pages = 0
            while pages < max(1, min(int(max_pages), 20)):
                pages += 1
                after = self.store.cursor()
                page = self.core.poll_events(
                    after=after,
                    limit=200,
                    consumer_id=self.consumer_id,
                    timeout=0,
                )
                events = [event for event in (page.get("events") or []) if isinstance(event, dict)]
                next_cursor = str(page.get("next_cursor") or after)
                self.store.ingest_events(events, next_cursor)
                event_ids = [str(event.get("event_id") or "") for event in events if event.get("event_id")]
                if event_ids:
                    try:
                        result = self.core.ack_events(self.consumer_id, event_ids)
                        acked += int(result.get("acked_count") or 0)
                    except CoreApiError as exc:
                        self.store.log(
                            "warn",
                            "core-sync",
                            "Events stored locally but Core acknowledgement failed",
                            {"code": exc.code, "message": exc.message, "event_ids": event_ids},
                        )
                total += len(events)
                if not events or not page.get("has_more"):
                    break
            self.last_sync = {
                "ok": True,
                "at": utc_now(),
                "events": total,
                "acked": acked,
                "pages": pages,
                "cursor": self.store.cursor(),
                "error": "",
            }
            if total:
                self.store.log("info", "core-sync", "Core events synchronized", self.last_sync)
            return dict(self.last_sync)
        except CoreApiError as exc:
            self.last_sync = {
                "ok": False,
                "at": utc_now(),
                "events": 0,
                "cursor": self.store.cursor(),
                "error": str(exc),
                "error_code": exc.code,
            }
            self.store.log("error", "core-sync", "Core event sync failed", self.last_sync)
            return dict(self.last_sync)
        finally:
            self._sync_lock.release()

    def status(self) -> dict[str, Any]:
        try:
            core_health = self.core.health(require_supported=True)
            core_ok = True
            core_error = ""
        except CoreApiError as exc:
            core_health = {}
            core_ok = False
            core_error = str(exc)
        accounts: list[dict[str, Any]] = []
        if core_ok:
            try:
                accounts = self.core.accounts()
            except CoreApiError as exc:
                core_ok = False
                core_error = str(exc)
        runtime_management: dict[str, Any] = {
            "supported": False,
            "configured": False,
            "available": False,
            "ok": False,
            "accounts": [],
            "error": "",
        }
        advertised = core_health.get("runtime_management") if isinstance(core_health, dict) else None
        if isinstance(advertised, dict):
            runtime_management.update(
                {
                    "supported": True,
                    "configured": bool(advertised.get("configured")),
                    "available": bool(advertised.get("available")),
                    "registry_hot_reload": bool(advertised.get("registry_hot_reload")),
                }
            )
            if core_ok and runtime_management["configured"] and runtime_management["available"]:
                try:
                    runtime_payload = self.core.runtime_accounts()
                    runtime_management["accounts"] = [
                        item for item in runtime_payload.get("accounts") or [] if isinstance(item, dict)
                    ]
                    runtime_management["registry_reload"] = runtime_payload.get("registry_reload") or {}
                    runtime_management["ok"] = True
                except CoreApiError as exc:
                    runtime_management["error"] = str(exc)
        summary = self.store.message_summary()
        return {
            "ok": core_ok,
            "service": "wechat-console",
            "contract_version": SUPPORTED_CONTRACT_VERSION,
            "generated_at": utc_now(),
            "core": {
                "required": True,
                "ok": core_ok,
                "url": self.core_url,
                "health": core_health,
                "error": core_error,
            },
            "accounts": accounts,
            "runtime_management": runtime_management,
            "desktop_url": self.desktop_url,
            "messages": summary,
            "saved_messages": self.store.saved_count(),
            "sync": self.last_sync,
            "integrations": self.integration_status(),
        }

    def integration_status(self) -> dict[str, Any]:
        return {
            "agent": _probe_optional("wechat-agent", self.agent_url),
            "efb": _probe_optional("efb-multi", self.efb_url),
        }

    def chats(self, account_id: str, query: str = "") -> dict[str, Any]:
        if not account_id:
            raise ValueError("account_id is required")
        payload = self.core.chats(account_id, query=query, limit=200)
        chats = [item for item in payload.get("chats") or [] if isinstance(item, dict)]
        projected = self.store.list_messages(account_id=account_id, limit=500)
        stats: dict[str, dict[str, Any]] = {}
        for message in projected:
            chat_id = str(message.get("chat_id") or "")
            bucket = stats.setdefault(chat_id, {"message_count": 0, "latest_message_at": ""})
            bucket["message_count"] += 1
            created = str(message.get("created_at") or "")
            if created > bucket["latest_message_at"]:
                bucket["latest_message_at"] = created
        for chat in chats:
            chat.update(stats.get(str(chat.get("chat_id") or ""), {}))
        payload["chats"] = chats
        return payload

    def save_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        account_id = _required_text(payload, "account_id")
        message_id = _required_text(payload, "message_id")
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else None
        stored = self.store.get_message(account_id, message_id)
        if snapshot is None:
            snapshot = stored
        if not isinstance(snapshot, dict):
            raise ValueError("message snapshot is unavailable; sync the message first or provide snapshot")
        snapshot_account = str(snapshot.get("account_id") or account_id)
        snapshot_message = str(snapshot.get("message_id") or message_id)
        if snapshot_account != account_id or snapshot_message != message_id:
            raise ValueError("snapshot identity does not match account_id/message_id")
        chat_id = str(payload.get("chat_id") or snapshot.get("chat_id") or "").strip()
        if not chat_id:
            raise ValueError("chat_id is required")
        item = self.store.save_message(
            account_id=account_id,
            chat_id=chat_id,
            message_id=message_id,
            snapshot=snapshot,
            title=str(payload.get("title") or ""),
            note=str(payload.get("note") or ""),
            tags=payload.get("tags"),
        )
        media_id = str(snapshot.get("media_id") or "").strip()
        if media_id:
            self.archive_saved_media(item["saved_message_id"], snapshot=snapshot)
            item = self.store.get_saved(item["saved_message_id"]) or item
        self.store.log(
            "info",
            "saved-messages",
            "Message saved",
            {"saved_message_id": item["saved_message_id"], "account_id": account_id, "message_id": message_id},
        )
        return item

    def archive_saved_media(self, saved_id: str, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        item = self.store.get_saved(saved_id)
        if not item:
            raise KeyError("saved message not found")
        snapshot = snapshot or item.get("snapshot") or {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        media_id = str(snapshot.get("media_id") or "").strip()
        if not media_id:
            return {"ok": True, "skipped": True, "reason": "message_has_no_media"}
        account_id = str(item.get("account_id") or snapshot.get("account_id") or "")
        filename = str(snapshot.get("filename") or media_id)
        mime_type = str(snapshot.get("mime_type") or "application/octet-stream")
        try:
            body, core_mime, core_filename = self.core.media(account_id, media_id)
            media = self.store.archive_media(
                saved_id=saved_id,
                account_id=account_id,
                media_id=media_id,
                filename=core_filename or filename,
                mime_type=core_mime or mime_type,
                body=body,
            )
            self.store.log(
                "info",
                "saved-messages",
                "Attachment archived",
                {"saved_message_id": saved_id, "media_id": media_id, "size_bytes": len(body)},
            )
            return {"ok": True, "media": media}
        except CoreApiError as exc:
            media = self.store.record_media_failure(
                saved_id=saved_id,
                account_id=account_id,
                media_id=media_id,
                filename=filename,
                mime_type=mime_type,
                error=str(exc),
            )
            self.store.log(
                "warn",
                "saved-messages",
                "Attachment archive failed",
                {"saved_message_id": saved_id, "media_id": media_id, "error": str(exc)},
            )
            return {"ok": False, "error": str(exc), "error_code": exc.code, "media": media}


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _probe_optional(name: str, base_url: str) -> dict[str, Any]:
    if not base_url:
        return {"name": name, "required": False, "configured": False, "ok": None, "url": "", "error": ""}
    started = time.monotonic()
    request = urllib.request.Request(base_url.rstrip("/") + "/health", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            response.read(4096)
            return {
                "name": name,
                "required": False,
                "configured": True,
                "ok": 200 <= response.status < 400,
                "url": base_url,
                "status": response.status,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error": "",
            }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {
            "name": name,
            "required": False,
            "configured": True,
            "ok": False,
            "url": base_url,
            "status": getattr(exc, "code", None),
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": str(exc),
        }


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(handler: BaseHTTPRequestHandler, text: str, status: int = 404) -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _query_text(query: dict[str, list[str]], key: str, default: str = "") -> str:
    return str((query.get(key) or [default])[0] or default).strip()


def _query_int(query: dict[str, list[str]], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int((query.get(key) or [default])[0])
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def create_handler(service: ConsoleService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WeChatConsole/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _body_json(self, *, max_bytes: int = 30 * 1024 * 1024) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0 or length > max_bytes:
                raise ValueError("request body too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body must be UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("request JSON must be an object")
            return payload

        def _handle_error(self, exc: Exception) -> None:
            if isinstance(exc, CoreApiError):
                _json_response(
                    self,
                    {"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
                    exc.status,
                )
                return
            if isinstance(exc, KeyError):
                _json_response(self, {"error": {"code": "not_found", "message": str(exc), "details": {}}}, 404)
                return
            if isinstance(exc, ValueError):
                _json_response(self, {"error": {"code": "invalid_request", "message": str(exc), "details": {}}}, 400)
                return
            service.store.log("error", "api", "Unhandled Console API error", {"error": str(exc), "path": self.path})
            _json_response(self, {"error": {"code": "internal_error", "message": str(exc), "details": {}}}, 500)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path == "/api/health":
                    status = service.status()
                    _json_response(self, status, 200 if status.get("ok") else 503)
                    return
                if path == "/api/status":
                    _json_response(self, service.status())
                    return
                if path == "/api/accounts":
                    _json_response(self, {"accounts": service.core.accounts()})
                    return
                if path == "/api/runtime/accounts":
                    _json_response(self, service.core.runtime_accounts())
                    return
                runtime_prefix = "/api/runtime/accounts/"
                if path.startswith(runtime_prefix):
                    runtime_suffix = path[len(runtime_prefix) :]
                    if runtime_suffix.endswith("/login/snapshot"):
                        account_id = unquote(runtime_suffix[: -len("/login/snapshot")].strip("/"))
                        if not account_id or "/" in account_id:
                            raise KeyError("endpoint not found")
                        body, mime_type = service.core.runtime_login_snapshot(account_id)
                        self.send_response(200)
                        self.send_header("Content-Type", mime_type)
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store, max-age=0")
                        self.send_header("Pragma", "no-cache")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if runtime_suffix.endswith("/login"):
                        account_id = unquote(runtime_suffix[: -len("/login")].strip("/"))
                        if account_id and "/" not in account_id:
                            _json_response(self, service.core.runtime_login(account_id))
                            return
                    if runtime_suffix.endswith("/desktop"):
                        account_id = unquote(runtime_suffix[: -len("/desktop")].strip("/"))
                        if account_id and "/" not in account_id:
                            _json_response(self, service.core.runtime_desktop(account_id))
                            return
                if path == "/api/chats":
                    _json_response(
                        self,
                        service.chats(_query_text(query, "account_id"), _query_text(query, "query")),
                    )
                    return
                if path == "/api/messages":
                    rows = service.store.list_messages(
                        account_id=_query_text(query, "account_id"),
                        chat_id=_query_text(query, "chat_id"),
                        query=_query_text(query, "query"),
                        message_type=_query_text(query, "type"),
                        limit=_query_int(query, "limit", 100, 1, 500),
                        include_removed=_query_text(query, "include_removed").lower() in {"1", "true", "yes"},
                    )
                    _json_response(self, {"messages": rows, "cursor": service.store.cursor()})
                    return
                if path == "/api/messages/summary":
                    _json_response(
                        self,
                        service.store.message_summary(
                            _query_text(query, "account_id"), _query_text(query, "chat_id")
                        ),
                    )
                    return
                if path == "/api/message-types":
                    summary = service.store.message_summary(
                        _query_text(query, "account_id"), _query_text(query, "chat_id")
                    )
                    _json_response(self, {"types": summary.get("types") or []})
                    return
                send_prefix = "/api/sends/"
                if path.startswith(send_prefix):
                    send_id = unquote(path[len(send_prefix) :])
                    if "/" not in send_id:
                        item = service.store.get_send(send_id)
                        if not item:
                            raise KeyError("send not found")
                        _json_response(self, item)
                        return
                if path == "/api/saved":
                    _json_response(
                        self,
                        {
                            "items": service.store.list_saved(
                                account_id=_query_text(query, "account_id"),
                                chat_id=_query_text(query, "chat_id"),
                                query=_query_text(query, "query"),
                                tag=_query_text(query, "tag"),
                                limit=_query_int(query, "limit", 100, 1, 500),
                            )
                        },
                    )
                    return
                saved_prefix = "/api/saved/"
                if path.startswith(saved_prefix):
                    saved_id = unquote(path[len(saved_prefix) :])
                    if "/" not in saved_id:
                        item = service.store.get_saved(saved_id)
                        if not item:
                            raise KeyError("saved message not found")
                        _json_response(self, item)
                        return
                media_prefix = "/api/media/"
                if path.startswith(media_prefix):
                    media_id = unquote(path[len(media_prefix) :])
                    account_id = _query_text(query, "account_id")
                    if not account_id:
                        raise ValueError("account_id is required")
                    body, mime_type, filename = service.core.media(account_id, media_id)
                    self.send_response(200)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Content-Disposition", f'inline; filename="{filename.replace(chr(34), "")}"')
                    self.send_header("Cache-Control", "private, max-age=60")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                saved_media_prefix = "/api/saved-media/"
                if path.startswith(saved_media_prefix):
                    saved_media_id = unquote(path[len(saved_media_prefix) :])
                    archived = service.store.archived_media_bytes(saved_media_id)
                    if not archived:
                        raise KeyError("archived media not found")
                    body, item = archived
                    self.send_response(200)
                    self.send_header("Content-Type", str(item.get("mime_type") or "application/octet-stream"))
                    self.send_header("Content-Length", str(len(body)))
                    filename = str(item.get("filename") or "attachment.bin").replace('"', "")
                    self.send_header("Content-Disposition", f'inline; filename="{filename}"')
                    self.send_header("Cache-Control", "private, max-age=86400")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/logs":
                    _json_response(
                        self,
                        {
                            "logs": service.store.logs(
                                limit=_query_int(query, "limit", 200, 1, 500),
                                level=_query_text(query, "level"),
                                category=_query_text(query, "category"),
                                query=_query_text(query, "query"),
                            )
                        },
                    )
                    return
                if path == "/api/integrations":
                    _json_response(self, service.integration_status())
                    return
                self._serve_static(parsed.path)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                self._handle_error(exc)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                payload = self._body_json()
                if path == "/api/events/sync":
                    _json_response(self, service.sync_events_once(max_pages=10))
                    return
                if path == "/api/runtime/accounts":
                    result = service.core.create_runtime_account(
                        account_id=_required_text(payload, "account_id"),
                        display_name=str(payload.get("display_name") or "").strip(),
                        display=str(payload.get("display") or "").strip(),
                        runtime_provider=str(payload.get("runtime_provider") or "agent_wechat").strip(),
                        autostart=bool(payload.get("autostart", True)),
                        start=bool(payload.get("start", True)),
                    )
                    service.store.log("info", "runtime", "WeChat account created", result)
                    _json_response(self, result, 201)
                    return
                runtime_prefix = "/api/runtime/accounts/"
                if path.startswith(runtime_prefix):
                    suffix = unquote(path[len(runtime_prefix) :])
                    if "/" in suffix:
                        account_id, action = suffix.rsplit("/", 1)
                        if action == "login":
                            result = service.core.runtime_login_start(account_id)
                        else:
                            result = service.core.runtime_account_action(account_id, action)
                        service.store.log("info", "runtime", f"WeChat account {action}", result)
                        _json_response(self, result)
                        return
                if path == "/api/send/text":
                    request_id = str(payload.get("client_request_id") or uuid.uuid4().hex)
                    result = service.core.send_text(
                        account_id=_required_text(payload, "account_id"),
                        chat_id=_required_text(payload, "chat_id"),
                        text=_required_text(payload, "text"),
                        target_message_id=str(payload.get("target_message_id") or ""),
                        mention_member_ids=[str(item) for item in payload.get("mention_member_ids") or []],
                        client_request_id=request_id,
                        idempotency_key=str(self.headers.get("Idempotency-Key") or request_id),
                    )
                    service.store.record_send_receipt(result)
                    service.store.log("info", "send", "Text accepted by Core", result)
                    _json_response(self, result, 202)
                    return
                if path in {"/api/send/image", "/api/send/file"}:
                    kind = path.rsplit("/", 1)[-1]
                    outgoing = dict(payload)
                    outgoing["account_id"] = _required_text(payload, "account_id")
                    outgoing["chat_id"] = _required_text(payload, "chat_id")
                    request_id = str(outgoing.get("client_request_id") or uuid.uuid4().hex)
                    outgoing["client_request_id"] = request_id
                    result = service.core.send_media(
                        kind,
                        outgoing,
                        idempotency_key=str(self.headers.get("Idempotency-Key") or request_id),
                    )
                    service.store.record_send_receipt(result)
                    service.store.log("info", "send", f"{kind.title()} accepted by Core", result)
                    _json_response(self, result, 202)
                    return
                if path == "/api/saved":
                    item = service.save_message(payload)
                    _json_response(self, item, 201)
                    return
                saved_prefix = "/api/saved/"
                if path.startswith(saved_prefix):
                    suffix = unquote(path[len(saved_prefix) :])
                    if suffix.endswith("/archive"):
                        saved_id = suffix[: -len("/archive")]
                        if not service.store.get_saved(saved_id):
                            raise KeyError("saved message not found")
                        _json_response(self, service.archive_saved_media(saved_id))
                        return
                    if "/" not in suffix:
                        item = service.store.update_saved(
                            suffix,
                            title=payload.get("title") if "title" in payload else None,
                            note=payload.get("note") if "note" in payload else None,
                            tags=payload.get("tags") if "tags" in payload else None,
                        )
                        if not item:
                            raise KeyError("saved message not found")
                        _json_response(self, item)
                        return
                raise KeyError("endpoint not found")
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                self._handle_error(exc)

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                runtime_prefix = "/api/runtime/accounts/"
                if path.startswith(runtime_prefix):
                    account_id = unquote(path[len(runtime_prefix) :])
                    if account_id and "/" not in account_id:
                        purge_data = _query_text(query, "purge_data").lower() in {"1", "true", "yes", "on"}
                        result = service.core.delete_runtime_account(account_id, purge_data=purge_data)
                        service.store.log("info", "runtime", "WeChat account removed", result)
                        _json_response(self, result)
                        return
                prefix = "/api/saved/"
                if path.startswith(prefix):
                    saved_id = unquote(path[len(prefix) :])
                    if "/" in saved_id or not service.store.delete_saved(saved_id):
                        raise KeyError("saved message not found")
                    service.store.log("info", "saved-messages", "Saved message deleted", {"saved_message_id": saved_id})
                    _json_response(self, {"ok": True, "saved_message_id": saved_id})
                    return
                raise KeyError("endpoint not found")
            except Exception as exc:
                self._handle_error(exc)

        def _serve_static(self, raw_path: str) -> None:
            if raw_path in {"", "/"}:
                target = STATIC_DIR / "index.html"
            else:
                rel = unquote(raw_path).lstrip("/")
                target = (STATIC_DIR / rel).resolve()
                root = STATIC_DIR.resolve()
                if target != root and root not in target.parents:
                    _text_response(self, "not found", 404)
                    return
                if not target.exists() and "." not in Path(rel).name:
                    target = STATIC_DIR / "index.html"
            if not target.exists() or not target.is_file():
                _text_response(self, "not found", 404)
                return
            mime, _ = mimetypes.guess_type(str(target))
            self.send_response(200)
            self.send_header("Content-Type", mime or "application/octet-stream")
            self.send_header("Content-Length", str(target.stat().st_size))
            self.send_header("Cache-Control", "no-cache" if target.suffix in {".html", ".js", ".css"} else "public, max-age=3600")
            self.end_headers()
            with target.open("rb") as file:
                shutil.copyfileobj(file, self.wfile)

    return Handler


def create_server(host: str, port: int, service: ConsoleService) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), create_handler(service))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the decoupled WeChat Hub Console")
    parser.add_argument("--host", default=os.environ.get("WECHAT_CONSOLE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WECHAT_CONSOLE_PORT", "8078")))
    parser.add_argument("--core-url", default=os.environ.get("WECHAT_CORE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--agent-url", default=os.environ.get("WECHAT_AGENT_URL", ""))
    parser.add_argument("--efb-url", default=os.environ.get("EFB_MULTI_URL", ""))
    parser.add_argument("--desktop-url", default=os.environ.get("WECHAT_DESKTOP_URL", ""))
    parser.add_argument("--db", default=os.environ.get("WECHAT_CONSOLE_DB", str(DEFAULT_DB)))
    parser.add_argument("--archive-dir", default=os.environ.get("WECHAT_CONSOLE_ARCHIVE_DIR", str(DEFAULT_ARCHIVE_DIR)))
    parser.add_argument(
        "--sync-interval",
        type=float,
        default=float(os.environ.get("WECHAT_CONSOLE_SYNC_INTERVAL", "2")),
    )
    parser.add_argument("--no-background-sync", action="store_true")
    args = parser.parse_args(argv)

    service = ConsoleService(
        core_url=args.core_url,
        db_path=args.db,
        archive_dir=args.archive_dir,
        agent_url=args.agent_url,
        efb_url=args.efb_url,
        desktop_url=args.desktop_url,
    )
    if not args.no_background_sync:
        service.start_background_sync(args.sync_interval)
    server = create_server(args.host, args.port, service)
    print(f"Serving WeChat Console at http://{args.host}:{args.port} (Core: {args.core_url})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
