"""Console RC.14 Storage Gates: T5 (Mirrored event compaction), T6 (Payload deprecation), T7 (Projection rebuild)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wechat_console.compaction import (
    apply_console_core_events_compaction,
    plan_console_core_events_compaction,
)
from wechat_console.rebuild_projection import rebuild_projection
from wechat_console.store import ConsoleStore, utc_now


class DummyCoreClient:
    """Mock Core client providing canonical query APIs for rebuild testing."""

    def __init__(self, canonical_messages: list[dict]):
        self._canonical_messages = canonical_messages

    def accounts(self) -> dict:
        acc_ids = sorted(list({m.get("account_id", "acc-1") for m in self._canonical_messages}))
        return {
            "accounts": [
                {"account_id": aid, "display_name": f"Account {aid}", "wechat_identity_uuid": f"ident-{aid}"}
                for aid in acc_ids
            ]
        }

    def chats(self, account_id: str) -> dict:
        chat_ids = sorted(list({
            m.get("chat_id") for m in self._canonical_messages
            if m.get("account_id", "acc-1") == account_id
        }))
        return {"chats": [{"chat_id": cid} for cid in chat_ids]}

    def messages(self, account_id: str, chat_id: str, cursor: str = "", limit: int = 100) -> dict:
        msgs = [
            m for m in self._canonical_messages
            if m.get("account_id", "acc-1") == account_id and m.get("chat_id") == chat_id
        ]
        return {"messages": msgs, "has_more": False, "next_cursor": ""}


class TestConsoleRC14Storage(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "console_test.db"
        self.archive_dir = Path(self.temp_dir.name) / "archive"
        self.store = ConsoleStore(self.db_path, self.archive_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_gate_t6_projection_payload_deprecation(self) -> None:
        """Gate T6: New message writes do not duplicate full message payload into payload_json."""
        now = utc_now()
        event = {
            "event_id": "evt-msg-1",
            "cursor": "1",
            "account_id": "acc-1",
            "event_type": "message.created",
            "occurred_at": now,
            "payload": {
                "message": {
                    "account_id": "acc-1",
                    "message_id": "msg-001",
                    "chat_id": "chat-001@chatroom",
                    "type": "text",
                    "direction": "incoming",
                    "created_at": now,
                    "author": {"member_id": "user-a", "display_name": "Alice"},
                    "text": "Hello world from RC14",
                    "media_id": "",
                    "filename": "",
                    "mime_type": "",
                    "target_message_id": "",
                }
            },
        }
        self.store.ingest_events([event], next_cursor="1")

        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM message_projection WHERE message_id='msg-001'").fetchone()

        self.assertIsNotNone(row)
        # Verify payload_json is "{}" (deprecated empty compatibility value)
        self.assertEqual(row["payload_json"], "{}")

        # Verify normalized fields are stored and retrieved correctly
        msg = self.store.get_message("acc-1", "msg-001")
        self.assertIsNotNone(msg)
        self.assertEqual(msg["message_id"], "msg-001")
        self.assertEqual(msg["text"], "Hello world from RC14")
        self.assertEqual(msg["author"]["display_name"], "Alice")
        self.assertEqual(msg["type"], "text")
        self.assertEqual(msg["direction"], "incoming")

        # list_messages works identically
        page = self.store.list_messages(account_id="acc-1", chat_id="chat-001@chatroom")
        self.assertEqual(len(page), 1)
        self.assertEqual(page[0]["message_id"], "msg-001")

    def test_gate_t5_console_mirrored_events_compaction(self) -> None:
        """Gate T5: Redundant status events removed from core_events; durable user data preserved."""
        now = utc_now()
        # Seed core_events with 10 status events (1 initial + 8 redundant + 1 final)
        # plus 2 non-status message events
        status_events = []
        for i in range(10):
            payload = {
                "account": {
                    "account_id": "acc-1",
                    "display_name": "Account 1",
                    "state": "online",
                    "runtime": {"logged_in": True},
                    "sync": {"cycle_count": i, "elapsed_ms": 100 + i},
                }
            }
            status_events.append((f"evt-s-{i}", str(i + 1), "acc-1", "account.status", now, json.dumps(payload), now))

        msg_events = [
            ("evt-m-1", "11", "acc-1", "message.created", now, json.dumps({"message": {"message_id": "m-1"}}), now),
            ("evt-m-2", "12", "acc-1", "message.created", now, json.dumps({"message": {"message_id": "m-2"}}), now),
        ]

        with self.store.connect() as conn:
            for ev in status_events + msg_events:
                conn.execute(
                    "INSERT INTO core_events (event_id, cursor, account_id, event_type, occurred_at, payload_json, ingested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ev,
                )

        # Seed durable user state
        self.store.save_message(
            account_id="acc-1",
            chat_id="chat-1",
            message_id="saved-m-1",
            snapshot={"text": "Permanent User Note"},
            title="Permanent Note Title",
            note="Important message bookmark",
        )
        saved_before = self.store.list_saved()
        self.assertEqual(len(saved_before), 1)

        # Run compaction plan
        plan = plan_console_core_events_compaction(self.store)
        self.assertEqual(plan["total_status_events"], 10)
        self.assertEqual(plan["redundant_candidate_rows"], 8)
        self.assertEqual(plan["total_preserved_rows"], 2)

        # Apply compaction
        res = apply_console_core_events_compaction(self.store, plan, confirmed=True)
        self.assertEqual(res["deleted_events"], 8)

        # Verify post-compaction core_events state
        with self.store.connect() as conn:
            remaining_events = conn.execute("SELECT event_id, event_type FROM core_events ORDER BY event_id").fetchall()
        remaining_ids = {r["event_id"] for r in remaining_events}
        # Non-status events preserved
        self.assertIn("evt-m-1", remaining_ids)
        self.assertIn("evt-m-2", remaining_ids)
        # First and newest status events preserved
        self.assertIn("evt-s-0", remaining_ids)
        self.assertIn("evt-s-9", remaining_ids)
        # Redundant events removed
        self.assertNotIn("evt-s-1", remaining_ids)
        self.assertNotIn("evt-s-5", remaining_ids)

        # Verify durable user data completely untouched
        saved_after = self.store.list_saved()
        self.assertEqual(len(saved_after), 1)
        self.assertEqual(saved_after[0]["snapshot"]["text"], "Permanent User Note")
        self.assertEqual(saved_after[0]["title"], "Permanent Note Title")

    def test_gate_t7_projection_rebuild(self) -> None:
        """Gate T7: Projection rebuild from canonical Core APIs with 100% field parity."""
        now = utc_now()
        canonical_messages = [
            {
                "account_id": "acc-1",
                "message_id": f"msg-canon-{i}",
                "chat_id": "chat-grp-1@chatroom",
                "instance_uuid": "inst-1",
                "wechat_identity_uuid": "ident-1",
                "type": "text",
                "direction": "incoming" if i % 2 == 0 else "outgoing",
                "created_at": f"2026-09-11T14:00:{i:02d}Z",
                "author": {"member_id": f"user-{i}", "display_name": f"User {i}"},
                "text": f"Canonical message {i}",
                "media_id": f"med-{i}" if i % 3 == 0 else "",
                "filename": "",
                "mime_type": "",
                "target_message_id": "",
            }
            for i in range(10)
        ]

        # Populate store initially via events
        for msg in canonical_messages:
            event = {
                "event_id": f"evt-{msg['message_id']}",
                "cursor": "1",
                "account_id": msg["account_id"],
                "event_type": "message.created",
                "occurred_at": msg["created_at"],
                "payload": {"message": msg},
            }
            self.store.ingest_events([event], next_cursor="1")

        # Save durable user message
        self.store.save_message(
            account_id="acc-1",
            chat_id="chat-grp-1@chatroom",
            message_id="msg-canon-1",
            snapshot={"text": "Saved bookmark"},
            title="Bookmark title",
            note="Bookmark note",
        )

        mock_core = DummyCoreClient(canonical_messages)

        # Rebuild projection
        rebuild_res = rebuild_projection(self.store, mock_core, swap=True)
        self.assertTrue(rebuild_res["ok"])
        self.assertEqual(rebuild_res["total_messages_rebuilt"], 10)
        self.assertTrue(rebuild_res["parity"]["parity_ok"])
        self.assertEqual(rebuild_res["parity"]["matched_count"], 10)
        self.assertEqual(rebuild_res["parity"]["mismatched_count"], 0)

        # Verify active projection after swap
        messages = self.store.list_messages(account_id="acc-1", chat_id="chat-grp-1@chatroom")
        self.assertEqual(len(messages), 10)
        self.assertEqual(messages[0]["wechat_identity_uuid"], "ident-1")

        # Verify saved messages preserved
        saved = self.store.list_saved()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["title"], "Bookmark title")
        self.assertEqual(saved[0]["snapshot"]["text"], "Saved bookmark")


if __name__ == "__main__":
    unittest.main()
