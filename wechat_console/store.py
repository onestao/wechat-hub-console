from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,，\n]", value)
    elif isinstance(value, list):
        raw = [str(item) for item in value]
    else:
        raw = [str(value)]
    seen: set[str] = set()
    output: list[str] = []
    for item in raw:
        tag = item.strip()
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        output.append(tag[:64])
        if len(output) >= 32:
            break
    return output


class ConsoleStore:
    """Console-owned durable state.

    This database is intentionally *not* Core's database.  It stores only a
    projection of events needed by the UI plus Saved Messages and Console logs.
    """

    def __init__(self, db_path: str | Path, archive_dir: str | Path) -> None:
        self.db_path = Path(db_path)
        self.archive_dir = Path(archive_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS console_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS core_events (
                    event_id TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_core_events_account_cursor
                    ON core_events(account_id, cursor);

                CREATE TABLE IF NOT EXISTS message_projection (
                    account_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    author_json TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    media_id TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    target_message_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    removed INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_message_projection_chat_time
                    ON message_projection(account_id, chat_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_message_projection_type
                    ON message_projection(account_id, chat_id, type);

                CREATE TABLE IF NOT EXISTS send_projection (
                    send_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    echo_message_id TEXT NOT NULL DEFAULT '',
                    delivery_certainty TEXT NOT NULL DEFAULT '',
                    automatic_retry INTEGER,
                    accepted_at TEXT NOT NULL DEFAULT '',
                    error_json TEXT NOT NULL DEFAULT '{}',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_send_projection_account_updated
                    ON send_projection(account_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS saved_messages (
                    saved_message_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    snapshot_json TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_saved_messages_updated
                    ON saved_messages(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_saved_messages_chat
                    ON saved_messages(account_id, chat_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS saved_message_media (
                    saved_media_id TEXT PRIMARY KEY,
                    saved_message_id TEXT NOT NULL REFERENCES saved_messages(saved_message_id) ON DELETE CASCADE,
                    account_id TEXT NOT NULL,
                    media_id TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                    archive_relpath TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER,
                    sha256 TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    UNIQUE(saved_message_id, media_id)
                );
                CREATE INDEX IF NOT EXISTS idx_saved_message_media_parent
                    ON saved_message_media(saved_message_id);

                CREATE TABLE IF NOT EXISTS console_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_console_logs_created
                    ON console_logs(created_at DESC);
                """
            )

    def set_meta(self, key: str, value: str) -> None:
        now = utc_now()
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO console_meta(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, now),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM console_meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def cursor(self) -> str:
        return self.get_meta("core_event_cursor", "")

    def ingest_events(self, events: list[dict[str, Any]], next_cursor: str) -> list[str]:
        ingested_ids: list[str] = []
        now = utc_now()
        with self._lock, self.connect() as conn:
            for event in events:
                event_id = str(event.get("event_id") or "").strip()
                if not event_id:
                    continue
                cursor = str(event.get("cursor") or "")
                account_id = str(event.get("account_id") or "")
                event_type = str(event.get("event_type") or "")
                occurred_at = str(event.get("occurred_at") or now)
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                inserted = conn.execute(
                    """
                    INSERT OR IGNORE INTO core_events
                        (event_id, cursor, account_id, event_type, occurred_at, payload_json, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, cursor, account_id, event_type, occurred_at, _json(payload), now),
                ).rowcount
                if inserted:
                    ingested_ids.append(event_id)
                self._apply_event(conn, event_type, account_id, payload, occurred_at)
            if next_cursor:
                conn.execute(
                    """
                    INSERT INTO console_meta(key, value, updated_at) VALUES ('core_event_cursor', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (str(next_cursor), now),
                )
        return ingested_ids

    def _apply_event(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        account_id: str,
        payload: dict[str, Any],
        occurred_at: str,
    ) -> None:
        if event_type == "send.updated":
            receipt = payload.get("send") if isinstance(payload.get("send"), dict) else payload
            if not isinstance(receipt, dict):
                return
            self._upsert_send_receipt_conn(
                conn,
                receipt,
                error=payload.get("error") if isinstance(payload.get("error"), dict) else {},
                details=payload.get("details") if isinstance(payload.get("details"), dict) else {},
                updated_at=occurred_at,
            )
            return
        if event_type in {"message.created", "message.updated"}:
            message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
            if not isinstance(message, dict):
                return
            message_id = str(message.get("message_id") or "").strip()
            chat_id = str(message.get("chat_id") or "").strip()
            scoped_account = str(message.get("account_id") or account_id).strip()
            if not message_id or not chat_id or not scoped_account:
                return
            author = message.get("author") if isinstance(message.get("author"), dict) else {}
            now = utc_now()
            conn.execute(
                """
                INSERT INTO message_projection (
                    account_id, message_id, chat_id, type, created_at, direction,
                    author_json, text, media_id, filename, mime_type, target_message_id,
                    payload_json, removed, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(account_id, message_id) DO UPDATE SET
                    chat_id=excluded.chat_id,
                    type=excluded.type,
                    created_at=excluded.created_at,
                    direction=excluded.direction,
                    author_json=excluded.author_json,
                    text=excluded.text,
                    media_id=excluded.media_id,
                    filename=excluded.filename,
                    mime_type=excluded.mime_type,
                    target_message_id=excluded.target_message_id,
                    payload_json=excluded.payload_json,
                    removed=0,
                    updated_at=excluded.updated_at
                """,
                (
                    scoped_account,
                    message_id,
                    chat_id,
                    str(message.get("type") or "unsupported"),
                    str(message.get("created_at") or occurred_at),
                    str(message.get("direction") or "incoming"),
                    _json(author),
                    str(message.get("text") or ""),
                    str(message.get("media_id") or ""),
                    str(message.get("filename") or ""),
                    str(message.get("mime_type") or ""),
                    str(message.get("target_message_id") or ""),
                    _json(message),
                    now,
                ),
            )
            return
        if event_type == "message.removed":
            message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
            if not isinstance(message, dict):
                return
            message_id = str(message.get("message_id") or payload.get("message_id") or "").strip()
            scoped_account = str(message.get("account_id") or account_id).strip()
            if message_id and scoped_account:
                conn.execute(
                    "UPDATE message_projection SET removed=1, updated_at=? WHERE account_id=? AND message_id=?",
                    (utc_now(), scoped_account, message_id),
                )

    def _upsert_send_receipt_conn(
        self,
        conn: sqlite3.Connection,
        receipt: dict[str, Any],
        *,
        error: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        updated_at: str | None = None,
    ) -> None:
        send_id = str(receipt.get("send_id") or "").strip()
        if not send_id:
            return
        conn.execute(
            """
            INSERT INTO send_projection (
                send_id, account_id, chat_id, kind, status, echo_message_id,
                delivery_certainty, automatic_retry, accepted_at,
                error_json, details_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(send_id) DO UPDATE SET
                account_id=excluded.account_id,
                chat_id=excluded.chat_id,
                kind=excluded.kind,
                status=excluded.status,
                echo_message_id=excluded.echo_message_id,
                delivery_certainty=excluded.delivery_certainty,
                automatic_retry=excluded.automatic_retry,
                accepted_at=CASE WHEN excluded.accepted_at<>'' THEN excluded.accepted_at ELSE send_projection.accepted_at END,
                error_json=excluded.error_json,
                details_json=excluded.details_json,
                updated_at=excluded.updated_at
            """,
            (
                send_id,
                str(receipt.get("account_id") or ""),
                str(receipt.get("chat_id") or ""),
                str(receipt.get("kind") or ""),
                str(receipt.get("status") or "accepted"),
                str(receipt.get("echo_message_id") or ""),
                str(receipt.get("delivery_certainty") or ""),
                None if "automatic_retry" not in receipt else int(bool(receipt.get("automatic_retry"))),
                str(receipt.get("accepted_at") or ""),
                _json(error or {}),
                _json(details or {}),
                updated_at or utc_now(),
            ),
        )

    def record_send_receipt(self, receipt: dict[str, Any]) -> None:
        with self._lock, self.connect() as conn:
            self._upsert_send_receipt_conn(conn, receipt)

    def get_send(self, send_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM send_projection WHERE send_id=?", (send_id,)).fetchone()
        if not row:
            return None
        try:
            error = json.loads(row["error_json"] or "{}")
        except json.JSONDecodeError:
            error = {}
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            details = {}
        output = {
            "send_id": row["send_id"],
            "account_id": row["account_id"],
            "chat_id": row["chat_id"],
            "kind": row["kind"],
            "status": row["status"],
            "echo_message_id": row["echo_message_id"],
            "delivery_certainty": row["delivery_certainty"],
            "accepted_at": row["accepted_at"],
            "error": error,
            "details": details,
            "updated_at": row["updated_at"],
        }
        if row["automatic_retry"] is not None:
            output["automatic_retry"] = bool(row["automatic_retry"])
        return output

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        try:
            author = json.loads(row["author_json"] or "{}")
        except json.JSONDecodeError:
            author = {}
        output = {
            "account_id": row["account_id"],
            "message_id": row["message_id"],
            "chat_id": row["chat_id"],
            "type": row["type"],
            "created_at": row["created_at"],
            "direction": row["direction"],
            "author": author,
            "text": row["text"],
            "media_id": row["media_id"],
            "filename": row["filename"],
            "mime_type": row["mime_type"],
            "target_message_id": row["target_message_id"],
            "removed": bool(row["removed"]),
            "payload": payload,
        }
        return output

    def get_message(self, account_id: str, message_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM message_projection WHERE account_id=? AND message_id=?",
                (account_id, message_id),
            ).fetchone()
        return self._message_from_row(row) if row else None

    def list_messages(
        self,
        *,
        account_id: str = "",
        chat_id: str = "",
        query: str = "",
        message_type: str = "",
        limit: int = 100,
        include_removed: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if account_id:
            clauses.append("account_id=?")
            params.append(account_id)
        if chat_id:
            clauses.append("chat_id=?")
            params.append(chat_id)
        if message_type:
            clauses.append("type=?")
            params.append(message_type)
        if query:
            clauses.append("(text LIKE ? OR filename LIKE ? OR author_json LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if not include_removed:
            clauses.append("removed=0")
        params.append(max(1, min(int(limit), 500)))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM message_projection WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def message_summary(self, account_id: str = "", chat_id: str = "") -> dict[str, Any]:
        clauses = ["removed=0"]
        params: list[Any] = []
        if account_id:
            clauses.append("account_id=?")
            params.append(account_id)
        if chat_id:
            clauses.append("chat_id=?")
            params.append(chat_id)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS n FROM message_projection WHERE {where}", params).fetchone()["n"]
            chats = conn.execute(
                f"SELECT COUNT(DISTINCT account_id || char(31) || chat_id) AS n FROM message_projection WHERE {where}",
                params,
            ).fetchone()["n"]
            types = conn.execute(
                f"SELECT type, COUNT(*) AS count FROM message_projection WHERE {where} GROUP BY type ORDER BY count DESC",
                params,
            ).fetchall()
        return {
            "messages": int(total or 0),
            "chats": int(chats or 0),
            "types": [{"type": row["type"], "count": row["count"]} for row in types],
            "cursor": self.cursor(),
        }

    def save_message(
        self,
        *,
        account_id: str,
        chat_id: str,
        message_id: str,
        snapshot: dict[str, Any],
        title: str = "",
        note: str = "",
        tags: Any = None,
    ) -> dict[str, Any]:
        now = utc_now()
        saved_id = f"saved-{uuid.uuid4().hex}"
        normalized_tags = normalize_tags(tags)
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "SELECT saved_message_id FROM saved_messages WHERE account_id=? AND message_id=?",
                (account_id, message_id),
            ).fetchone()
            if existing:
                saved_id = str(existing["saved_message_id"])
                conn.execute(
                    """
                    UPDATE saved_messages
                    SET title=?, note=?, tags_json=?, updated_at=?
                    WHERE saved_message_id=?
                    """,
                    (title.strip(), note.strip(), _json(normalized_tags), now, saved_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO saved_messages (
                        saved_message_id, account_id, chat_id, message_id, title, note,
                        tags_json, snapshot_json, saved_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        saved_id,
                        account_id,
                        chat_id,
                        message_id,
                        title.strip(),
                        note.strip(),
                        _json(normalized_tags),
                        _json(snapshot),
                        now,
                        now,
                    ),
                )
        item = self.get_saved(saved_id)
        assert item is not None
        return item

    def update_saved(self, saved_id: str, *, title: Any = None, note: Any = None, tags: Any = None) -> dict[str, Any] | None:
        assignments: list[str] = []
        params: list[Any] = []
        if title is not None:
            assignments.append("title=?")
            params.append(str(title).strip())
        if note is not None:
            assignments.append("note=?")
            params.append(str(note).strip())
        if tags is not None:
            assignments.append("tags_json=?")
            params.append(_json(normalize_tags(tags)))
        if assignments:
            assignments.append("updated_at=?")
            params.append(utc_now())
            params.append(saved_id)
            with self._lock, self.connect() as conn:
                conn.execute(
                    f"UPDATE saved_messages SET {', '.join(assignments)} WHERE saved_message_id=?",
                    params,
                )
        return self.get_saved(saved_id)

    def delete_saved(self, saved_id: str) -> bool:
        media = self.saved_media(saved_id)
        with self._lock, self.connect() as conn:
            deleted = conn.execute("DELETE FROM saved_messages WHERE saved_message_id=?", (saved_id,)).rowcount
        if deleted:
            for item in media:
                relpath = str(item.get("archive_relpath") or "")
                if not relpath:
                    continue
                path = self._archive_path(relpath)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                (self.archive_dir / saved_id).rmdir()
            except OSError:
                pass
        return bool(deleted)

    def get_saved(self, saved_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM saved_messages WHERE saved_message_id=?", (saved_id,)).fetchone()
        if not row:
            return None
        return self._saved_from_row(row, include_media=True)

    def list_saved(
        self,
        *,
        account_id: str = "",
        chat_id: str = "",
        query: str = "",
        tag: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if account_id:
            clauses.append("account_id=?")
            params.append(account_id)
        if chat_id:
            clauses.append("chat_id=?")
            params.append(chat_id)
        if query:
            clauses.append("(title LIKE ? OR note LIKE ? OR snapshot_json LIKE ? OR tags_json LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like, like])
        if tag:
            clauses.append("tags_json LIKE ?")
            params.append(f"%{tag}%")
        params.append(max(1, min(int(limit), 500)))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM saved_messages WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._saved_from_row(row, include_media=True) for row in rows]

    def saved_count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM saved_messages").fetchone()["n"])

    def _saved_from_row(self, row: sqlite3.Row, *, include_media: bool) -> dict[str, Any]:
        try:
            tags = json.loads(row["tags_json"] or "[]")
        except json.JSONDecodeError:
            tags = []
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except json.JSONDecodeError:
            snapshot = {}
        item = {
            "saved_message_id": row["saved_message_id"],
            "account_id": row["account_id"],
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "title": row["title"],
            "note": row["note"],
            "tags": tags if isinstance(tags, list) else [],
            "snapshot": snapshot if isinstance(snapshot, dict) else {},
            "saved_at": row["saved_at"],
            "updated_at": row["updated_at"],
        }
        if include_media:
            item["media"] = self.saved_media(str(row["saved_message_id"]))
        return item

    def saved_media(self, saved_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM saved_message_media WHERE saved_message_id=? ORDER BY archived_at, saved_media_id",
                (saved_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _safe_filename(value: str) -> str:
        name = Path(value or "attachment.bin").name
        cleaned = re.sub(r"[^A-Za-z0-9._()\-\u4e00-\u9fff]+", "_", name).strip("._")
        return (cleaned or "attachment.bin")[:160]

    def _archive_path(self, relpath: str) -> Path:
        target = (self.archive_dir / relpath).resolve()
        root = self.archive_dir.resolve()
        if target != root and root not in target.parents:
            raise ValueError("archive path escaped root")
        return target

    def archive_media(
        self,
        *,
        saved_id: str,
        account_id: str,
        media_id: str,
        filename: str,
        mime_type: str,
        body: bytes,
    ) -> dict[str, Any]:
        safe_name = self._safe_filename(filename or media_id)
        digest = hashlib.sha256(body).hexdigest()
        relpath = f"{saved_id}/{digest[:12]}-{safe_name}"
        target = self._archive_path(relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.tmp")
        temp.write_bytes(body)
        os.replace(temp, target)
        now = utc_now()
        saved_media_id = f"saved-media-{uuid.uuid4().hex}"
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "SELECT saved_media_id FROM saved_message_media WHERE saved_message_id=? AND media_id=?",
                (saved_id, media_id),
            ).fetchone()
            if existing:
                saved_media_id = str(existing["saved_media_id"])
                conn.execute(
                    """
                    UPDATE saved_message_media
                    SET account_id=?, filename=?, mime_type=?, archive_relpath=?, size_bytes=?,
                        sha256=?, status='archived', error='', archived_at=?
                    WHERE saved_media_id=?
                    """,
                    (account_id, safe_name, mime_type, relpath, len(body), digest, now, saved_media_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO saved_message_media (
                        saved_media_id, saved_message_id, account_id, media_id, filename,
                        mime_type, archive_relpath, size_bytes, sha256, status, error, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'archived', '', ?)
                    """,
                    (
                        saved_media_id,
                        saved_id,
                        account_id,
                        media_id,
                        safe_name,
                        mime_type or "application/octet-stream",
                        relpath,
                        len(body),
                        digest,
                        now,
                    ),
                )
        return self.saved_media_item(saved_media_id) or {}

    def record_media_failure(
        self,
        *,
        saved_id: str,
        account_id: str,
        media_id: str,
        filename: str,
        mime_type: str,
        error: str,
    ) -> dict[str, Any]:
        saved_media_id = f"saved-media-{uuid.uuid4().hex}"
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "SELECT saved_media_id, status FROM saved_message_media WHERE saved_message_id=? AND media_id=?",
                (saved_id, media_id),
            ).fetchone()
            if existing:
                saved_media_id = str(existing["saved_media_id"])
                if str(existing["status"] or "") == "archived":
                    return dict(
                        conn.execute(
                            "SELECT * FROM saved_message_media WHERE saved_media_id=?",
                            (saved_media_id,),
                        ).fetchone()
                    )
                conn.execute(
                    """
                    UPDATE saved_message_media
                    SET account_id=?, filename=?, mime_type=?, status='archive_failed', error=?
                    WHERE saved_media_id=?
                    """,
                    (account_id, self._safe_filename(filename or media_id), mime_type, error[:1000], saved_media_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO saved_message_media (
                        saved_media_id, saved_message_id, account_id, media_id, filename,
                        mime_type, status, error
                    ) VALUES (?, ?, ?, ?, ?, ?, 'archive_failed', ?)
                    """,
                    (
                        saved_media_id,
                        saved_id,
                        account_id,
                        media_id,
                        self._safe_filename(filename or media_id),
                        mime_type or "application/octet-stream",
                        error[:1000],
                    ),
                )
        return self.saved_media_item(saved_media_id) or {}

    def saved_media_item(self, saved_media_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM saved_message_media WHERE saved_media_id=?", (saved_media_id,)).fetchone()
        return dict(row) if row else None

    def archived_media_bytes(self, saved_media_id: str) -> tuple[bytes, dict[str, Any]] | None:
        item = self.saved_media_item(saved_media_id)
        if not item or item.get("status") != "archived" or not item.get("archive_relpath"):
            return None
        path = self._archive_path(str(item["archive_relpath"]))
        if not path.exists() or not path.is_file():
            return None
        return path.read_bytes(), item

    def log(self, level: str, category: str, message: str, details: dict[str, Any] | None = None) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO console_logs(created_at, level, category, message, details_json) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), level, category, message, _json(details or {})),
            )
            conn.execute(
                "DELETE FROM console_logs WHERE log_id NOT IN (SELECT log_id FROM console_logs ORDER BY log_id DESC LIMIT 5000)"
            )

    def logs(self, *, limit: int = 200, level: str = "", category: str = "", query: str = "") -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if level:
            clauses.append("level=?")
            params.append(level)
        if category:
            clauses.append("category=?")
            params.append(category)
        if query:
            clauses.append("(message LIKE ? OR details_json LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        params.append(max(1, min(int(limit), 500)))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM console_logs WHERE {' AND '.join(clauses)} ORDER BY log_id DESC LIMIT ?",
                params,
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            output.append(item)
        return output
