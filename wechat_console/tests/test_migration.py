from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from wechat_console.store import ConsoleStore


OLD_CONSOLE_SCHEMA = """
CREATE TABLE console_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE core_events (
    event_id TEXT PRIMARY KEY,
    cursor TEXT NOT NULL,
    account_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE INDEX idx_core_events_account_cursor
    ON core_events(account_id, cursor);

CREATE TABLE message_projection (
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
CREATE INDEX idx_message_projection_chat_time
    ON message_projection(account_id, chat_id, created_at DESC);
CREATE INDEX idx_message_projection_type
    ON message_projection(account_id, chat_id, type);

CREATE TABLE send_projection (
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
CREATE INDEX idx_send_projection_account_updated
    ON send_projection(account_id, updated_at DESC);

CREATE TABLE saved_messages (
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
CREATE INDEX idx_saved_messages_updated
    ON saved_messages(updated_at DESC);
CREATE INDEX idx_saved_messages_chat
    ON saved_messages(account_id, chat_id, updated_at DESC);

CREATE TABLE saved_message_media (
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
CREATE INDEX idx_saved_message_media_parent
    ON saved_message_media(saved_message_id);

CREATE TABLE console_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_console_logs_created
    ON console_logs(created_at DESC);
"""


class ConsoleSchemaMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "console.sqlite"
        self.archive_dir = self.root / "archive"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @contextlib.contextmanager
    def _raw_conn(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _get_columns(self, table: str) -> set[str]:
        with self._raw_conn() as conn:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return {row["name"] for row in rows}

    def _get_indexes(self, table: str) -> set[str]:
        with self._raw_conn() as conn:
            rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
            return {row["name"] for row in rows}

    def test_fresh_database_initialization(self) -> None:
        store = ConsoleStore(self.db_path, self.archive_dir)
        try:
            cols = self._get_columns("message_projection")
            self.assertIn("instance_uuid", cols)
            self.assertIn("wechat_identity_uuid", cols)
            indexes = self._get_indexes("message_projection")
            self.assertIn("idx_message_projection_identity_chat_time", indexes)
            self.assertIn("idx_message_projection_instance_chat_time", indexes)
        finally:
            store.close()

    def test_upgrade_from_old_schema_without_identity_columns(self) -> None:
        # 1. Create DB with old schema (pre-Identity v2)
        with self._raw_conn() as conn:
            conn.executescript(OLD_CONSOLE_SCHEMA)
            # Insert pre-existing message
            conn.execute(
                """
                INSERT INTO message_projection(
                    account_id, message_id, chat_id, type, created_at, direction,
                    author_json, text, payload_json, updated_at
                ) VALUES (
                    'f-live-a', 'msg-001', 'chat-100', 'text', '2026-09-08T12:00:00Z', 'incoming',
                    '{"name":"User"}', 'Historical message prior to upgrade', '{}', '2026-09-08T12:00:00Z'
                )
                """
            )
        
        # Verify old columns exist, identity columns do NOT exist
        old_cols = self._get_columns("message_projection")
        self.assertNotIn("instance_uuid", old_cols)
        self.assertNotIn("wechat_identity_uuid", old_cols)

        # 2. Run ConsoleStore initialization (the upgrade path)
        store = ConsoleStore(self.db_path, self.archive_dir)
        try:
            # 3. Verify columns and indexes now exist
            new_cols = self._get_columns("message_projection")
            self.assertIn("instance_uuid", new_cols)
            self.assertIn("wechat_identity_uuid", new_cols)

            indexes = self._get_indexes("message_projection")
            self.assertIn("idx_message_projection_identity_chat_time", indexes)
            self.assertIn("idx_message_projection_instance_chat_time", indexes)

            # 4. Verify historical data is preserved and defaults are set
            messages = store.list_messages(account_id="f-live-a")
            self.assertEqual(len(messages), 1)
            msg = messages[0]
            self.assertEqual(msg["message_id"], "msg-001")
            self.assertEqual(msg["text"], "Historical message prior to upgrade")
            self.assertEqual(msg["instance_uuid"], "")
            self.assertEqual(msg["wechat_identity_uuid"], "")
        finally:
            store.close()

    def test_repeated_initialization_idempotency(self) -> None:
        # Initialize twice on old schema
        with self._raw_conn() as conn:
            conn.executescript(OLD_CONSOLE_SCHEMA)

        store1 = ConsoleStore(self.db_path, self.archive_dir)
        try:
            # Re-initialize multiple times
            store1.initialize()
        finally:
            store1.close()

        store2 = ConsoleStore(self.db_path, self.archive_dir)
        try:
            store2.initialize()
            cols = self._get_columns("message_projection")
            self.assertIn("instance_uuid", cols)
            self.assertIn("wechat_identity_uuid", cols)
        finally:
            store2.close()

    def test_populated_database_projection_and_filtering(self) -> None:
        # Prepopulate old schema
        with self._raw_conn() as conn:
            conn.executescript(OLD_CONSOLE_SCHEMA)
            for i in range(10):
                conn.execute(
                    f"""
                    INSERT INTO message_projection(
                        account_id, message_id, chat_id, type, created_at, direction,
                        author_json, text, payload_json, updated_at
                    ) VALUES (
                        'acc-1', 'msg-{i:03d}', 'chat-1', 'text', '2026-09-08T12:{i:02d}:00Z', 'incoming',
                        '{{}}', 'Message {i}', '{{}}', '2026-09-08T12:{i:02d}:00Z'
                    )
                    """
                )

        store = ConsoleStore(self.db_path, self.archive_dir)
        try:
            res = store.list_messages(account_id="acc-1")
            self.assertEqual(len(res), 10)

            # Now project a new message via ingest_events that has instance_uuid and wechat_identity_uuid
            store.ingest_events(
                [
                    {
                        "event_id": "evt-new",
                        "cursor": "100",
                        "account_id": "acc-1",
                        "event_type": "message.created",
                        "occurred_at": "2026-09-09T00:00:00Z",
                        "payload": {
                            "message": {
                                "message_id": "msg-new-001",
                                "chat_id": "chat-1",
                                "type": "text",
                                "created_at": "2026-09-09T00:00:00Z",
                                "direction": "outgoing",
                                "text": "New Identity V2 message",
                                "instance_uuid": "inst-uuid-1234",
                                "wechat_identity_uuid": "ident-uuid-5678",
                            }
                        },
                    }
                ],
                next_cursor="101",
            )

            # Query by identity
            by_ident = store.list_messages(wechat_identity_uuid="ident-uuid-5678")
            self.assertEqual(len(by_ident), 1)
            self.assertEqual(by_ident[0]["message_id"], "msg-new-001")
            self.assertEqual(by_ident[0]["instance_uuid"], "inst-uuid-1234")

            # Query by instance
            by_inst = store.list_messages(instance_uuid="inst-uuid-1234")
            self.assertEqual(len(by_inst), 1)
            self.assertEqual(by_inst[0]["message_id"], "msg-new-001")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
