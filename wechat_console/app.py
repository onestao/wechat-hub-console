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
import re
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
    from .agent_client import AgentApiError, AgentClient
    from .core_client import CoreApiError, CoreClient, SUPPORTED_CONTRACT_VERSION
    from .store import ConsoleStore, utc_now
except ImportError:  # pragma: no cover - supports `python wechat_console/app.py`
    from agent_client import AgentApiError, AgentClient
    from core_client import CoreApiError, CoreClient, SUPPORTED_CONTRACT_VERSION
    from store import ConsoleStore, utc_now


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
_runtime_dir = os.environ.get("WECHAT_CONSOLE_RUNTIME_DIR", "").strip()
DEFAULT_RUNTIME_DIR = Path(_runtime_dir) if _runtime_dir else PACKAGE_DIR.parent / "runtime" / "wechat-console"
DEFAULT_DB = DEFAULT_RUNTIME_DIR / "console.sqlite"
DEFAULT_ARCHIVE_DIR = DEFAULT_RUNTIME_DIR / "saved-attachments"

AVATAR_CACHE_TTL_SECONDS = 300.0
AVATAR_NEGATIVE_TTL_SECONDS = 30.0

# A send is "settled" once it can no longer change without a new user action.
TERMINAL_SEND_STATES = frozenset({"sent", "failed", "uncertain"})

# Product-facing text for a failed send.  The Console never renders an internal
# exception or traceback to a normal user; Core supplies ``user_message`` and
# this map is the local fallback for older/unknown codes.
SEND_FAILURE_DISPLAY: dict[str, str] = {
    "wechat_not_ready": "微信正在完成登录，请稍候。",
    "wechat_unavailable": "微信客户端当前不可用，请稍后重试。",
    "sender_unavailable": "发送服务暂不可用，请稍后重试。",
    "send_timeout": "发送超时，请稍后重试。",
    "target_unavailable": "目标会话已不可用，请刷新后重试。",
    "operator_gui_busy": "微信界面正在被手动操作，请稍后重试。",
    "delivery_confirmation_timeout": "微信已接收提交，但未能确认送达结果，请核对后决定是否重发。",
    "sender_interrupted": "发送过程被中断，送达状态未知，请核对后决定是否重发。",
    "agent_wechat_delivery_unknown": "未能确认微信是否已接收，请核对后决定是否重发。",
    "sender_failed": "发送失败，请稍后重试。",
}


def send_display_message(item: dict[str, Any]) -> str:
    """Human-readable reason for a non-``sent`` send, never a raw traceback."""

    status = str(item.get("status") or "")
    if status == "sent":
        return ""
    error = item.get("error") if isinstance(item.get("error"), dict) else {}
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    failure = details.get("failure") if isinstance(details.get("failure"), dict) else {}
    for candidate in (failure.get("user_message"), error.get("user_message")):
        if str(candidate or "").strip():
            return str(candidate).strip()
    code = str(failure.get("code") or error.get("code") or "").strip()
    if code and code in SEND_FAILURE_DISPLAY:
        return SEND_FAILURE_DISPLAY[code]
    if status == "uncertain":
        return "未能确认微信是否已接收，请核对后决定是否重发。"
    if status == "failed":
        return SEND_FAILURE_DISPLAY["sender_failed"]
    return ""
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_ALLOWED_ID = re.compile(r"^[0-9a-zA-Z_@.-]{1,128}$")


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
        self.agent = AgentClient(agent_url)
        self.agent_url = agent_url.rstrip("/")
        self.efb_url = efb_url.rstrip("/")
        self.desktop_url = desktop_url.rstrip("/")
        self.consumer_id = consumer_id
        self._stop = threading.Event()
        self._sync_thread: threading.Thread | None = None
        self._sync_lock = threading.Lock()
        self._bootstrap_attempted = False
        self._avatar_cache: dict[str, tuple[float, bytes, str]] = {}
        self._avatar_negative: dict[str, float] = {}
        self._avatar_lock = threading.Lock()
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

    def ensure_consumer_bootstrap(self) -> dict[str, Any]:
        """Perform the Console's governed Core consumer bootstrap exactly once.

        Core refuses a cold poll at cursor 0 for a registered consumer
        (``missing_bootstrap_provenance``).  Without this, the Console's event
        sync fails on every cycle, ``core_events`` stays empty, and a send that
        Core already failed keeps showing "正在排队发送…" forever.
        """

        self._bootstrap_attempted = True
        result = self.core.bootstrap_consumer(self.consumer_id, mode="at_head")
        initial_cursor = result.get("initial_cursor")
        if initial_cursor is not None:
            # Core assigns the server-side initial cursor; polling below it is
            # rejected, so adopt it locally before the next poll.
            self.store.set_cursor(str(initial_cursor))
        self.store.log("info", "core-sync", "Console Core consumer bootstrapped", result)
        return result

    def send_status(self, send_id: str) -> dict[str, Any] | None:
        """Return a send receipt, reconciling a non-terminal local projection.

        The event stream is the primary convergence path; this is the bounded
        backstop so a send can never be displayed as ``queued`` indefinitely
        because an update event was missed.
        """

        item = self.store.get_send(send_id)
        if item is None:
            try:
                remote = self.core.send_status(send_id)
            except CoreApiError:
                return None
            self.store.record_core_send_status(remote)
            item = self.store.get_send(send_id)
        elif str(item.get("status") or "") not in TERMINAL_SEND_STATES:
            try:
                remote = self.core.send_status(send_id)
            except CoreApiError:
                remote = None
            if isinstance(remote, dict) and str(remote.get("status") or ""):
                if str(remote.get("status")) != str(item.get("status")) or remote.get("error_code"):
                    self.store.record_core_send_status(remote)
                    item = self.store.get_send(send_id)
        if item is None:
            return None
        return {**item, "display_message": send_display_message(item)}

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
                try:
                    page = self.core.poll_events(
                        after=after,
                        limit=200,
                        consumer_id=self.consumer_id,
                        timeout=0,
                    )
                except CoreApiError as exc:
                    if str(exc.code) != "missing_bootstrap_provenance" or self._bootstrap_attempted:
                        raise
                    # First-party Console with no local cursor: perform the
                    # governed bootstrap, adopt the server-assigned cursor and
                    # retry the same page instead of dropping the cycle.
                    self.ensure_consumer_bootstrap()
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
                has_more = bool(page.get("has_more"))
                stream_head = page.get("stream_head_cursor")
                checkpoint_cursor = int(next_cursor) if has_more else (int(stream_head) if stream_head is not None else int(next_cursor))
                try:
                    last_id = event_ids[-1] if event_ids else ""
                    self.core.checkpoint_events(
                        self.consumer_id,
                        checkpoint_cursor,
                        last_event_id=last_id,
                    )
                except Exception as exc:
                    self.store.log(
                        "warn",
                        "core-sync",
                        "Checkpoint reporting failed; local cursor retained",
                        {"error": str(exc), "cursor": checkpoint_cursor},
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

    def identity_avatar(self, wechat_identity_uuid: str) -> tuple[bytes, str]:
        """Serve a WeChat avatar through Console (Identity v2 contract §2.2).

        The browser never trusts an external image URL: the source is resolved
        exclusively from Core account payloads keyed by
        ``wechat_identity_uuid``, fetched server-side with strict size and
        content-type limits, and re-served same-origin with a short TTL cache.
        """
        identity_uuid = str(wechat_identity_uuid or "").strip()
        if not AVATAR_ALLOWED_ID.match(identity_uuid):
            raise KeyError("avatar not found")
        now = time.monotonic()
        with self._avatar_lock:
            cached = self._avatar_cache.get(identity_uuid)
            if cached and now - cached[0] < AVATAR_CACHE_TTL_SECONDS:
                return cached[1], cached[2]
            if now - self._avatar_negative.get(identity_uuid, float("-inf")) < AVATAR_NEGATIVE_TTL_SECONDS:
                raise KeyError("avatar not found")
        avatar_url = self._resolve_identity_avatar_url(identity_uuid)
        if avatar_url:
            try:
                body, mime_type = _fetch_avatar(avatar_url)
                with self._avatar_lock:
                    self._avatar_cache[identity_uuid] = (now, body, mime_type)
                    if len(self._avatar_cache) > 64:  # bounded cache: drop oldest entries
                        for key, _ in sorted(self._avatar_cache.items())[: len(self._avatar_cache) - 64]:
                            self._avatar_cache.pop(key, None)
                return body, mime_type
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
                pass
        # Fallback to Core avatar pipeline (for contacts, group members, self profile)
        try:
            body, mime_type = self.core.avatar(identity_uuid)
            with self._avatar_lock:
                self._avatar_cache[identity_uuid] = (now, body, mime_type)
                if len(self._avatar_cache) > 64:
                    for key, _ in sorted(self._avatar_cache.items())[: len(self._avatar_cache) - 64]:
                        self._avatar_cache.pop(key, None)
            return body, mime_type
        except Exception:
            pass

        with self._avatar_lock:
            self._avatar_negative[identity_uuid] = now
        raise KeyError("avatar not found")

    def _resolve_identity_avatar_url(self, identity_uuid: str) -> str:
        try:
            accounts = self.core.accounts()
        except CoreApiError:
            return ""
        for account in accounts:
            if str(account.get("wechat_identity_uuid") or "") != identity_uuid:
                continue
            profile = account.get("wechat_profile")
            url = str(profile.get("avatar_url") or "").strip() if isinstance(profile, dict) else ""
            if url.startswith(("https://", "http://")):
                return url
            return ""
        return ""

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

    def resolve_identity_uuid(self, *, account_id: str = "", wechat_identity_uuid: str = "") -> str:
        if wechat_identity_uuid:
            return wechat_identity_uuid.strip()
        try:
            accounts = self.core.accounts()
        except Exception:
            return ""
        if account_id:
            for acc in accounts:
                if acc.get("account_id") == account_id:
                    return str(acc.get("wechat_identity_uuid") or "")
        elif len(accounts) == 1:
            return str(accounts[0].get("wechat_identity_uuid") or "")
        return ""

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


def _agent_write(service: ConsoleService, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    """Dispatch one Agent automation upsert; returns (result, kind, id)."""
    if path == "/api/agent/monitors":
        result = service.agent.upsert_monitor(payload)
        return result, "monitor", str(result.get("monitor_id") or "")
    if path == "/api/agent/schedules":
        result = service.agent.upsert_schedule(payload)
        return result, "schedule", str(result.get("schedule_id") or "")
    if path == "/api/agent/templates":
        result = service.agent.upsert_template(payload)
        return result, "template", str(result.get("template_id") or "")
    raise KeyError("endpoint not found")


def _agent_delete(service: ConsoleService, path: str) -> tuple[str, str]:
    """Dispatch one Agent automation delete; returns (kind, id)."""
    if path.startswith("/api/agent/monitors/"):
        monitor_id = unquote(path[len("/api/agent/monitors/") :])
        if not monitor_id or "/" in monitor_id:
            raise KeyError("endpoint not found")
        service.agent.delete_monitor(monitor_id)
        return "monitor", monitor_id
    if path.startswith("/api/agent/schedules/"):
        schedule_id = unquote(path[len("/api/agent/schedules/") :])
        if not schedule_id or "/" in schedule_id:
            raise KeyError("endpoint not found")
        service.agent.delete_schedule(schedule_id)
        return "schedule", schedule_id
    if path.startswith("/api/agent/templates/"):
        template_id = unquote(path[len("/api/agent/templates/") :])
        if not template_id or "/" in template_id:
            raise KeyError("endpoint not found")
        service.agent.delete_template(template_id)
        return "template", template_id
    raise KeyError("endpoint not found")


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


def _fetch_avatar(url: str) -> tuple[bytes, str]:
    """Fetch avatar bytes server-side under strict size/content-type limits."""
    request = urllib.request.Request(url, headers={"Accept": "image/*"})
    with urllib.request.urlopen(request, timeout=5.0) as response:
        mime_type = response.headers.get_content_type() or ""
        if not mime_type.startswith("image/"):
            raise ValueError("avatar source is not an image")
        body = response.read(AVATAR_MAX_BYTES + 1)
    if not body or len(body) > AVATAR_MAX_BYTES:
        raise ValueError("avatar payload exceeds the safe size limit")
    return body, mime_type


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
            if isinstance(exc, AgentApiError):
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
                    if runtime_suffix and "/" not in runtime_suffix:
                        # GET /api/runtime/accounts/{id} — identity-enriched detail
                        _json_response(self, service.core.account_detail(unquote(runtime_suffix)))
                        return
                avatar_prefix = "/api/avatar/"
                if path.startswith(avatar_prefix) or path.startswith("/v1/avatar/"):
                    prefix = avatar_prefix if path.startswith(avatar_prefix) else "/v1/avatar/"
                    avatar_key = unquote(path[len(prefix) :].strip("/"))
                    if not avatar_key:
                        raise KeyError("endpoint not found")
                    body, mime_type = service.identity_avatar(avatar_key)
                    self.send_response(200)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "private, max-age=300")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path.startswith("/v1/identities/") and path.endswith("/avatar"):
                    body, mime_type = service.core.avatar(path)
                    self.send_response(200)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/chats":
                    _json_response(
                        self,
                        service.chats(_query_text(query, "account_id"), _query_text(query, "query")),
                    )
                    return
                if path == "/api/contacts":
                    account_id = _query_text(query, "account_id")
                    wechat_identity_uuid = _query_text(query, "wechat_identity_uuid")
                    if not wechat_identity_uuid:
                        wechat_identity_uuid = service.resolve_identity_uuid(
                            account_id=account_id, wechat_identity_uuid=wechat_identity_uuid
                        )
                    if not wechat_identity_uuid:
                        if account_id:
                            raise ValueError(f"No identity bound for account {account_id}")
                        _json_response(self, {"contacts": [], "next_cursor": "", "has_more": False, "wechat_identity_uuid": ""})
                        return
                    search_query = _query_text(query, "query")
                    cursor = _query_text(query, "cursor")
                    limit = _query_int(query, "limit", 100, 1, 500)
                    result = service.core.identity_contacts(
                        wechat_identity_uuid,
                        query=search_query,
                        limit=limit,
                        cursor=cursor,
                    )
                    _json_response(self, result)
                    return
                chats_prefix = "/api/chats/"
                if path.startswith(chats_prefix) and path.endswith("/members"):
                    chat_id = unquote(path[len(chats_prefix) : -len("/members")].strip("/"))
                    account_id = _query_text(query, "account_id")
                    wechat_identity_uuid = _query_text(query, "wechat_identity_uuid")
                    if not wechat_identity_uuid:
                        wechat_identity_uuid = service.resolve_identity_uuid(
                            account_id=account_id, wechat_identity_uuid=wechat_identity_uuid
                        )
                    if not wechat_identity_uuid:
                        raise ValueError("account_id or wechat_identity_uuid is required")
                    search_query = _query_text(query, "query")
                    cursor = _query_text(query, "cursor")
                    limit = _query_int(query, "limit", 200, 1, 500)
                    result = service.core.identity_members(
                        wechat_identity_uuid,
                        chat_id,
                        query=search_query,
                        limit=limit,
                        cursor=cursor,
                    )
                    _json_response(self, result)
                    return
                if path == "/api/identity/profile":
                    account_id = _query_text(query, "account_id")
                    wechat_identity_uuid = _query_text(query, "wechat_identity_uuid")
                    if not wechat_identity_uuid:
                        wechat_identity_uuid = service.resolve_identity_uuid(
                            account_id=account_id, wechat_identity_uuid=wechat_identity_uuid
                        )
                    if not wechat_identity_uuid:
                        raise ValueError("account_id or wechat_identity_uuid is required")
                    result = service.core.identity_profile(wechat_identity_uuid)
                    _json_response(self, result)
                    return
                if path == "/api/messages":
                    instance_uuid = _query_text(query, "instance_uuid")
                    wechat_identity_uuid = _query_text(query, "wechat_identity_uuid")
                    account_id = _query_text(query, "account_id")
                    chat_id = _query_text(query, "chat_id")
                    before = _query_text(query, "before") or _query_text(query, "cursor")
                    limit = _query_int(query, "limit", 100, 1, 500)
                    msg_type = _query_text(query, "type")
                    search_query = _query_text(query, "query")
                    include_removed = _query_text(query, "include_removed").lower() in {"1", "true", "yes"}

                    page = service.store.list_messages(
                        account_id=account_id,
                        instance_uuid=instance_uuid,
                        wechat_identity_uuid=wechat_identity_uuid,
                        chat_id=chat_id,
                        query=search_query,
                        message_type=msg_type,
                        limit=limit,
                        before=before,
                        include_removed=include_removed,
                    )
                    _json_response(
                        self,
                        {
                            "messages": list(page),
                            "cursor": service.store.cursor(),
                            "next_cursor": getattr(page, "next_cursor", ""),
                            "has_more": getattr(page, "has_more", False),
                        },
                    )
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
                        item = service.send_status(send_id)
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
                agent_prefix = "/api/agent/"
                if path.startswith(agent_prefix):
                    self._handle_agent_get(path, query)
                    return
                self._serve_static(parsed.path)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                self._handle_error(exc)

        def _handle_agent_get(self, path: str, query: dict[str, list[str]]) -> None:
            """Proxy the optional Agent automation surface; failures stay structured."""
            if path == "/api/agent/status":
                payload = service.agent.status()
                _json_response(self, {"ok": True, "agent": payload})
                return
            if path == "/api/agent/monitors":
                _json_response(self, {"monitors": service.agent.monitors()})
                return
            if path == "/api/agent/schedules":
                _json_response(self, {"schedules": service.agent.schedules()})
                return
            if path == "/api/agent/templates":
                _json_response(self, {"templates": service.agent.templates()})
                return
            if path.startswith("/api/agent/monitors/"):
                monitor_id = unquote(path[len("/api/agent/monitors/") :])
                if monitor_id.endswith("/runs") and "/" not in monitor_id[: -len("/runs")]:
                    _json_response(self, service.agent.monitor_runs(monitor_id[: -len("/runs")]))
                    return
                raise KeyError("endpoint not found")
            if path.startswith("/api/agent/schedules/"):
                schedule_id = unquote(path[len("/api/agent/schedules/") :])
                if schedule_id.endswith("/runs") and "/" not in schedule_id[: -len("/runs")]:
                    _json_response(self, service.agent.schedule_runs(schedule_id[: -len("/runs")]))
                    return
                raise KeyError("endpoint not found")
            raise KeyError("endpoint not found")

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
                            service.store.log("info", "runtime", f"WeChat account {action}", result)
                        elif action == "update":
                            result = service.core.runtime_account_update(
                                account_id,
                                display_name=_required_text(payload, "display_name"),
                            )
                            service.store.log("info", "runtime", "WeChat account updated", result)
                        elif action == "confirm-switch":
                            result = service.core.confirm_identity_switch(
                                account_id,
                                observed_wechat_user_id=str(
                                    payload.get("observed_wechat_user_id") or ""
                                ).strip(),
                            )
                            service.store.log("info", "identity", "Identity switch confirmed", result)
                        else:
                            result = service.core.runtime_account_action(account_id, action)
                            service.store.log("info", "runtime", f"WeChat account {action}", result)
                        _json_response(self, result)
                        return
                if path == "/api/send/text":
                    request_id = str(payload.get("client_request_id") or uuid.uuid4().hex)
                    expected_identity = str(
                        payload.get("expected_wechat_identity_uuid") or payload.get("wechat_identity_uuid") or ""
                    ).strip()
                    result = service.core.send_text(
                        account_id=_required_text(payload, "account_id"),
                        chat_id=_required_text(payload, "chat_id"),
                        text=_required_text(payload, "text"),
                        target_message_id=str(payload.get("target_message_id") or ""),
                        mention_member_ids=[str(item) for item in payload.get("mention_member_ids") or []],
                        client_request_id=request_id,
                        idempotency_key=str(self.headers.get("Idempotency-Key") or request_id),
                        expected_wechat_identity_uuid=expected_identity,
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
                    expected_identity = str(
                        payload.get("expected_wechat_identity_uuid") or payload.get("wechat_identity_uuid") or ""
                    ).strip()
                    result = service.core.send_media(
                        kind,
                        outgoing,
                        idempotency_key=str(self.headers.get("Idempotency-Key") or request_id),
                        expected_wechat_identity_uuid=expected_identity,
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
                agent_prefix = "/api/agent/"
                if path.startswith(agent_prefix):
                    result, kind, resource_id = _agent_write(service, path, payload)
                    _json_response(self, result, 200)
                    service.store.log(
                        "info",
                        "automation",
                        f"Agent {kind} saved via Console",
                        {"kind": kind, "id": resource_id, "agent_url": service.agent_url},
                    )
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
                agent_prefix = "/api/agent/"
                if path.startswith(agent_prefix):
                    kind, resource_id = _agent_delete(service, path)
                    _json_response(self, {"ok": True, "kind": kind, "id": resource_id})
                    service.store.log(
                        "info",
                        "automation",
                        f"Agent {kind} deleted via Console",
                        {"kind": kind, "id": resource_id, "agent_url": service.agent_url},
                    )
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
