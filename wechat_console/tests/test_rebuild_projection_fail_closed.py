"""Mandatory tests for Console RC.14 Projection Rebuild Fail-Closed hardening.

Covers all requirements in docs/RC14_CONSOLE_PROJECTION_REBUILD_FAIL_CLOSED_TASKBOOK.md:
1. exact strict parity -> swap PASS
2. missing shadow row -> swap refused
3. extra shadow row -> swap refused
4. field mismatch -> swap refused
5. failed gate leaves original projection byte/logically unchanged
6. identity-enrichment-only fixture -> repair plan PASS
7. identity conflict -> repair plan FAIL
8. two accounts/two identities with overlapping chat/message-like identifiers remain isolated
9. non-empty Saved Messages/media remain identical
10. repeated identity repair is idempotent
11. after local repair fixture, the same identity-scoped message query used by the UI returns the expected rows
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from wechat_console.rebuild_projection import (
    create_shadow_table,
    insert_shadow_message,
    rebuild_projection,
    verify_projection_parity,
)
from wechat_console.store import ConsoleStore, utc_now


class MockCoreClient:
    """Configurable mock Core client for projection rebuild testing."""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        accounts: list[dict[str, Any]] | None = None,
    ):
        self._messages = messages
        if accounts is not None:
            self._accounts = accounts
        else:
            acc_ids = sorted(list({str(m.get("account_id") or "acc-1") for m in messages}))
            self._accounts = []
            for aid in acc_ids:
                matching_msgs = [m for m in messages if str(m.get("account_id") or "acc-1") == aid]
                ident = ""
                inst = ""
                for m in matching_msgs:
                    if m.get("wechat_identity_uuid"):
                        ident = str(m["wechat_identity_uuid"])
                    if m.get("instance_uuid"):
                        inst = str(m["instance_uuid"])
                self._accounts.append({
                    "account_id": aid,
                    "display_name": f"Account {aid}",
                    "wechat_identity_uuid": ident or f"ident-{aid}",
                    "instance_uuid": inst or f"inst-{aid}",
                })

    def accounts(self) -> dict[str, Any]:
        return {"accounts": self._accounts}

    def chats(self, account_id: str) -> dict[str, Any]:
        chat_ids = sorted(list({
            str(m.get("chat_id") or "")
            for m in self._messages
            if str(m.get("account_id") or "acc-1") == account_id and m.get("chat_id")
        }))
        return {"chats": [{"chat_id": cid} for cid in chat_ids]}

    def messages(self, account_id: str, chat_id: str, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        matched = [
            m for m in self._messages
            if str(m.get("account_id") or "acc-1") == account_id and str(m.get("chat_id") or "") == chat_id
        ]
        return {"messages": matched, "has_more": False, "next_cursor": ""}


def make_msg(
    account_id: str = "acc-1",
    message_id: str = "msg-001",
    chat_id: str = "chat-1@chatroom",
    instance_uuid: str = "inst-1",
    wechat_identity_uuid: str = "ident-1",
    text: str = "Hello test message",
    msg_type: str = "text",
    direction: str = "incoming",
    created_at: str = "2026-09-13T10:00:00Z",
    author: dict[str, Any] | None = None,
    media_id: str = "",
    filename: str = "",
    mime_type: str = "",
    target_message_id: str = "",
    removed: int = 0,
) -> dict[str, Any]:
    if author is None:
        author = {"member_id": f"user-{message_id}", "display_name": f"User {message_id}"}
    return {
        "account_id": account_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "instance_uuid": instance_uuid,
        "wechat_identity_uuid": wechat_identity_uuid,
        "type": msg_type,
        "direction": direction,
        "created_at": created_at,
        "author": author,
        "text": text,
        "media_id": media_id,
        "filename": filename,
        "mime_type": mime_type,
        "target_message_id": target_message_id,
        "removed": removed,
    }


class TestRebuildProjectionFailClosed(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "console_test.db"
        self.archive_dir = Path(self.temp_dir.name) / "archive"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.store = ConsoleStore(self.db_path, self.archive_dir)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _seed_active_messages(self, messages: list[dict[str, Any]]) -> None:
        now = utc_now()
        events = [
            {
                "event_id": f"evt-{m['message_id']}",
                "cursor": str(i + 1),
                "account_id": m["account_id"],
                "event_type": "message.created",
                "occurred_at": m.get("created_at") or now,
                "payload": {"message": m},
            }
            for i, m in enumerate(messages)
        ]
        self.store.ingest_events(events, next_cursor=str(len(messages)))

    def _projection_checksum(self) -> str:
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT account_id, message_id, chat_id, instance_uuid, wechat_identity_uuid, "
                "type, created_at, direction, author_json, text, media_id, filename, "
                "mime_type, target_message_id, removed "
                "FROM message_projection ORDER BY account_id, message_id"
            ).fetchall()
            serialized = json.dumps([dict(r) for r in rows], sort_keys=True)
            return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def test_1_exact_strict_parity_swap_pass(self) -> None:
        """Requirement 1: Exact strict parity -> swap PASS."""
        messages = [
            make_msg(message_id=f"msg-{i:03d}", text=f"Message {i}")
            for i in range(5)
        ]
        self._seed_active_messages(messages)
        core = MockCoreClient(messages)

        res = rebuild_projection(self.store, core, swap=True, mode="strict")
        self.assertTrue(res["ok"])
        self.assertTrue(res["swapped"])
        self.assertTrue(res["parity"]["parity_ok"])
        self.assertEqual(res["parity"]["matched_count"], 5)
        self.assertEqual(res["parity"]["mismatched_count"], 0)
        self.assertEqual(len(res["parity"]["missing_in_shadow"]), 0)
        self.assertEqual(len(res["parity"]["extra_in_shadow"]), 0)

        # Verify active projection is intact and queryable
        active = self.store.list_messages(account_id="acc-1", chat_id="chat-1@chatroom")
        self.assertEqual(len(active), 5)

    def test_2_missing_shadow_row_swap_refused(self) -> None:
        """Requirement 2: Missing shadow row -> swap refused."""
        msg1 = make_msg(message_id="msg-001")
        msg2 = make_msg(message_id="msg-002")
        self._seed_active_messages([msg1, msg2])

        # Core only returns msg1; msg2 is missing in shadow
        core = MockCoreClient([msg1])

        res = rebuild_projection(self.store, core, swap=True, mode="strict")
        self.assertFalse(res["ok"])
        self.assertFalse(res["swapped"])
        self.assertFalse(res["parity"]["parity_ok"])
        self.assertIn(("acc-1", "msg-002"), res["parity"]["missing_in_shadow"])

        # Verify active table was NOT modified
        active = self.store.list_messages(account_id="acc-1", chat_id="chat-1@chatroom")
        self.assertEqual(len(active), 2)

    def test_3_extra_shadow_row_swap_refused(self) -> None:
        """Requirement 3: Extra shadow row -> swap refused."""
        msg1 = make_msg(message_id="msg-001")
        self._seed_active_messages([msg1])

        # Core returns msg1 and an extra msg2 not present in active projection
        msg2 = make_msg(message_id="msg-002", text="Extra row from Core")
        core = MockCoreClient([msg1, msg2])

        res = rebuild_projection(self.store, core, swap=True, mode="strict")
        self.assertFalse(res["ok"])
        self.assertFalse(res["swapped"])
        self.assertFalse(res["parity"]["parity_ok"])
        self.assertIn(("acc-1", "msg-002"), res["parity"]["extra_in_shadow"])

        # Active table remains strictly unchanged (only 1 row)
        active = self.store.list_messages(account_id="acc-1", chat_id="chat-1@chatroom")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["message_id"], "msg-001")

    def test_4_field_mismatch_swap_refused(self) -> None:
        """Requirement 4: Field mismatch -> swap refused."""
        msg_active = make_msg(message_id="msg-001", text="Canonical active text")
        self._seed_active_messages([msg_active])

        # Core returns msg-001 with altered text
        msg_shadow = make_msg(message_id="msg-001", text="Tampered shadow text")
        core = MockCoreClient([msg_shadow])

        res = rebuild_projection(self.store, core, swap=True, mode="strict")
        self.assertFalse(res["ok"])
        self.assertFalse(res["swapped"])
        self.assertFalse(res["parity"]["parity_ok"])
        self.assertEqual(res["parity"]["mismatched_count"], 1)

        # Active text remains unchanged
        msg = self.store.get_message("acc-1", "msg-001")
        self.assertEqual(msg["text"], "Canonical active text")

    def test_5_failed_gate_leaves_original_projection_unchanged(self) -> None:
        """Requirement 5: Failed gate leaves original projection byte/logically unchanged."""
        messages = [
            make_msg(message_id=f"msg-{i:03d}", text=f"Important message {i}")
            for i in range(4)
        ]
        self._seed_active_messages(messages)
        original_checksum = self._projection_checksum()

        # Attempt 1: failure via missing row
        core_missing = MockCoreClient(messages[:2])
        res1 = rebuild_projection(self.store, core_missing, swap=True, mode="strict")
        self.assertFalse(res1["ok"])
        self.assertEqual(self._projection_checksum(), original_checksum)

        # Attempt 2: failure via extra row
        core_extra = MockCoreClient(messages + [make_msg(message_id="msg-999")])
        res2 = rebuild_projection(self.store, core_extra, swap=True, mode="strict")
        self.assertFalse(res2["ok"])
        self.assertEqual(self._projection_checksum(), original_checksum)

        # Attempt 3: failure via field mismatch
        tampered = list(messages)
        tampered[0] = make_msg(message_id="msg-000", text="Altered body")
        core_mismatch = MockCoreClient(tampered)
        res3 = rebuild_projection(self.store, core_mismatch, swap=True, mode="strict")
        self.assertFalse(res3["ok"])
        self.assertEqual(self._projection_checksum(), original_checksum)

    def test_6_identity_enrichment_only_fixture_pass(self) -> None:
        """Requirement 6: Identity-enrichment-only fixture -> repair plan PASS."""
        # Active projection messages have blank instance_uuid and wechat_identity_uuid (RC.14 bug state)
        active_msgs = [
            make_msg(
                message_id=f"msg-{i:03d}",
                text=f"Message {i}",
                instance_uuid="",
                wechat_identity_uuid="",
            )
            for i in range(3)
        ]
        self._seed_active_messages(active_msgs)

        # Core returns canonical nonblank identity values
        canonical_msgs = [
            make_msg(
                message_id=f"msg-{i:03d}",
                text=f"Message {i}",
                instance_uuid="inst-canonical-01",
                wechat_identity_uuid="ident-canonical-01",
            )
            for i in range(3)
        ]
        accounts = [{
            "account_id": "acc-1",
            "display_name": "Account 1",
            "wechat_identity_uuid": "ident-canonical-01",
            "instance_uuid": "inst-canonical-01",
        }]
        core = MockCoreClient(canonical_msgs, accounts=accounts)

        # In strict mode, this MUST fail because identities differ
        res_strict = rebuild_projection(self.store, core, swap=True, mode="strict")
        self.assertFalse(res_strict["ok"])
        self.assertFalse(res_strict["swapped"])

        # In explicit opt-in identity_enrichment mode, this MUST succeed
        res_enrich = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertTrue(res_enrich["ok"])
        self.assertTrue(res_enrich["swapped"])
        self.assertTrue(res_enrich["parity"]["parity_ok"])
        self.assertEqual(res_enrich["parity"]["enriched_count"], 3)
        self.assertEqual(res_enrich["parity"]["mismatched_count"], 0)

        # Verify active messages now carry the canonical identities
        for i in range(3):
            msg = self.store.get_message("acc-1", f"msg-{i:03d}")
            self.assertEqual(msg["instance_uuid"], "inst-canonical-01")
            self.assertEqual(msg["wechat_identity_uuid"], "ident-canonical-01")

    def test_7_identity_conflict_repair_plan_fail(self) -> None:
        """Requirement 7: Identity conflict -> repair plan FAIL."""
        # Active message already has identity A
        msg_active = make_msg(
            message_id="msg-001",
            instance_uuid="inst-A",
            wechat_identity_uuid="ident-A",
        )
        self._seed_active_messages([msg_active])

        # Core returns identity B (conflict: nonblank A -> nonblank B)
        msg_shadow = make_msg(
            message_id="msg-001",
            instance_uuid="inst-B",
            wechat_identity_uuid="ident-B",
        )
        accounts = [{
            "account_id": "acc-1",
            "wechat_identity_uuid": "ident-B",
            "instance_uuid": "inst-B",
        }]
        core = MockCoreClient([msg_shadow], accounts=accounts)

        res = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertFalse(res["ok"])
        self.assertFalse(res["swapped"])
        self.assertFalse(res["parity"]["parity_ok"])
        self.assertEqual(res["parity"]["mismatched_count"], 1)

        # Active identity remains unchanged
        msg = self.store.get_message("acc-1", "msg-001")
        self.assertEqual(msg["wechat_identity_uuid"], "ident-A")
        self.assertEqual(msg["instance_uuid"], "inst-A")

    def test_8_two_accounts_overlapping_identifiers_isolated(self) -> None:
        """Requirement 8: Two accounts/two identities with overlapping identifiers remain isolated."""
        # Account 1 and Account 2 both have message_id='msg-common-id' in chat_id='chat-shared'
        msg_acc1 = make_msg(
            account_id="acc-1",
            message_id="msg-common-id",
            chat_id="chat-shared",
            instance_uuid="",
            wechat_identity_uuid="",
            text="Acc 1 private text",
        )
        msg_acc2 = make_msg(
            account_id="acc-2",
            message_id="msg-common-id",
            chat_id="chat-shared",
            instance_uuid="",
            wechat_identity_uuid="",
            text="Acc 2 private text",
        )
        self._seed_active_messages([msg_acc1, msg_acc2])

        canon_acc1 = make_msg(
            account_id="acc-1",
            message_id="msg-common-id",
            chat_id="chat-shared",
            instance_uuid="inst-alpha",
            wechat_identity_uuid="ident-alpha",
            text="Acc 1 private text",
        )
        canon_acc2 = make_msg(
            account_id="acc-2",
            message_id="msg-common-id",
            chat_id="chat-shared",
            instance_uuid="inst-beta",
            wechat_identity_uuid="ident-beta",
            text="Acc 2 private text",
        )
        accounts = [
            {"account_id": "acc-1", "wechat_identity_uuid": "ident-alpha", "instance_uuid": "inst-alpha"},
            {"account_id": "acc-2", "wechat_identity_uuid": "ident-beta", "instance_uuid": "inst-beta"},
        ]
        core = MockCoreClient([canon_acc1, canon_acc2], accounts=accounts)

        res = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertTrue(res["ok"])
        self.assertTrue(res["swapped"])
        self.assertTrue(res["parity"]["parity_ok"])

        # Check isolation
        m1 = self.store.get_message("acc-1", "msg-common-id")
        m2 = self.store.get_message("acc-2", "msg-common-id")
        self.assertEqual(m1["wechat_identity_uuid"], "ident-alpha")
        self.assertEqual(m1["instance_uuid"], "inst-alpha")
        self.assertEqual(m1["text"], "Acc 1 private text")

        self.assertEqual(m2["wechat_identity_uuid"], "ident-beta")
        self.assertEqual(m2["instance_uuid"], "inst-beta")
        self.assertEqual(m2["text"], "Acc 2 private text")

    def test_9_nonempty_saved_messages_and_media_remain_identical(self) -> None:
        """Requirement 9: Non-empty Saved Messages/media remain identical."""
        # 1. Seed active messages with blank identity
        msg = make_msg(
            message_id="msg-saved-target",
            instance_uuid="",
            wechat_identity_uuid="",
            text="Bookmark text",
        )
        self._seed_active_messages([msg])

        # 2. Seed non-empty durable state in saved_messages
        self.store.save_message(
            account_id="acc-1",
            chat_id="chat-1@chatroom",
            message_id="msg-saved-target",
            snapshot={"text": "Bookmark text", "author": "Alice"},
            title="User Permanent Bookmark",
            note="Preserve this bookmark note across projection rebuild",
        )

        # 3. Seed non-empty durable state in saved_message_media + physical file
        dummy_file = self.archive_dir / "acc-1" / "media_sample.bin"
        dummy_file.parent.mkdir(parents=True, exist_ok=True)
        dummy_file_bytes = b"EXACT_MEDIA_ATTACHMENT_DATA_VERIFY_NO_MUTATION"
        dummy_file.write_bytes(dummy_file_bytes)
        dummy_sha256 = hashlib.sha256(dummy_file_bytes).hexdigest()

        with self.store.connect() as conn:
            saved_id = conn.execute("SELECT saved_message_id FROM saved_messages").fetchone()["saved_message_id"]
            conn.execute(
                """
                INSERT INTO saved_message_media (
                    saved_media_id, saved_message_id, account_id, media_id,
                    filename, mime_type, archive_relpath, size_bytes, sha256, status, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "med-001",
                    saved_id,
                    "acc-1",
                    "media-target",
                    "attachment.bin",
                    "application/octet-stream",
                    "acc-1/media_sample.bin",
                    len(dummy_file_bytes),
                    dummy_sha256,
                    "downloaded",
                    utc_now(),
                ),
            )
            # 4. Seed send_projection
            conn.execute(
                """
                INSERT INTO send_projection (
                    send_id, account_id, chat_id, kind, status, echo_message_id,
                    delivery_certainty, automatic_retry, accepted_at, error_json, details_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "send-001",
                    "acc-1",
                    "chat-1@chatroom",
                    "text",
                    "delivered",
                    "echo-001",
                    "confirmed",
                    0,
                    utc_now(),
                    "{}",
                    json.dumps({"info": "send record"}),
                    utc_now(),
                ),
            )
            # 5. Seed console_logs
            conn.execute(
                """
                INSERT INTO console_logs (created_at, level, category, message, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (utc_now(), "INFO", "audit", "pre-rebuild audit log", json.dumps({"tag": "audit"})),
            )

        # Snapshot durable state before rebuild
        saved_before = self.store.list_saved()
        with self.store.connect() as conn:
            media_before = [dict(r) for r in conn.execute("SELECT * FROM saved_message_media").fetchall()]
            send_before = [dict(r) for r in conn.execute("SELECT * FROM send_projection").fetchall()]
            logs_before = [dict(r) for r in conn.execute("SELECT * FROM console_logs").fetchall()]

        # Rebuild projection
        canon_msg = make_msg(
            message_id="msg-saved-target",
            instance_uuid="inst-1",
            wechat_identity_uuid="ident-1",
            text="Bookmark text",
        )
        accounts = [{
            "account_id": "acc-1",
            "wechat_identity_uuid": "ident-1",
            "instance_uuid": "inst-1",
        }]
        core = MockCoreClient([canon_msg], accounts=accounts)
        res = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertTrue(res["ok"])
        self.assertTrue(res["swapped"])

        # Verify durable state after rebuild
        saved_after = self.store.list_saved()
        self.assertEqual(len(saved_after), 1)
        self.assertEqual(saved_after[0]["title"], "User Permanent Bookmark")
        self.assertEqual(saved_after[0]["note"], "Preserve this bookmark note across projection rebuild")
        self.assertEqual(saved_after[0]["snapshot"]["text"], "Bookmark text")

        with self.store.connect() as conn:
            media_after = [dict(r) for r in conn.execute("SELECT * FROM saved_message_media").fetchall()]
            send_after = [dict(r) for r in conn.execute("SELECT * FROM send_projection").fetchall()]
            logs_after = [dict(r) for r in conn.execute("SELECT * FROM console_logs").fetchall()]

        self.assertEqual(media_before, media_after)
        self.assertEqual(send_before, send_after)
        self.assertEqual(logs_before, logs_after)
        self.assertEqual(dummy_file.read_bytes(), dummy_file_bytes)

    def test_10_repeated_identity_repair_is_idempotent(self) -> None:
        """Requirement 10: Repeated identity repair is idempotent."""
        active_msgs = [
            make_msg(message_id="msg-001", instance_uuid="", wechat_identity_uuid=""),
            make_msg(message_id="msg-002", instance_uuid="", wechat_identity_uuid=""),
        ]
        self._seed_active_messages(active_msgs)

        canon_msgs = [
            make_msg(message_id="msg-001", instance_uuid="inst-prod", wechat_identity_uuid="ident-prod"),
            make_msg(message_id="msg-002", instance_uuid="inst-prod", wechat_identity_uuid="ident-prod"),
        ]
        accounts = [{
            "account_id": "acc-1",
            "wechat_identity_uuid": "ident-prod",
            "instance_uuid": "inst-prod",
        }]
        core = MockCoreClient(canon_msgs, accounts=accounts)

        # Run 1
        res1 = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertTrue(res1["ok"])
        self.assertTrue(res1["swapped"])
        self.assertEqual(res1["parity"]["enriched_count"], 2)
        checksum_after_run1 = self._projection_checksum()

        # Run 2 (repeated on already repaired database)
        res2 = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertTrue(res2["ok"])
        self.assertTrue(res2["swapped"])
        self.assertTrue(res2["parity"]["parity_ok"])
        self.assertEqual(res2["parity"]["mismatched_count"], 0)
        checksum_after_run2 = self._projection_checksum()

        self.assertEqual(checksum_after_run1, checksum_after_run2)

        # Run 3 in strict mode (must now also pass because all fields match)
        res3 = rebuild_projection(self.store, core, swap=True, mode="strict")
        self.assertTrue(res3["ok"])
        self.assertTrue(res3["swapped"])
        self.assertEqual(self._projection_checksum(), checksum_after_run1)

    def test_11_ui_identity_scoped_message_query_returns_expected_rows(self) -> None:
        """Requirement 11: After local repair fixture, UI identity-scoped query returns expected rows."""
        # 1. Seed active messages with blank identity (reproducing production defect where UI shows 0 rows)
        messages = [
            make_msg(
                message_id=f"msg-ui-{i}",
                chat_id="chat-ui@chatroom",
                instance_uuid="",
                wechat_identity_uuid="",
                text=f"Chat message {i}",
            )
            for i in range(5)
        ]
        self._seed_active_messages(messages)

        # 2. Verify that BEFORE repair, UI identity query returns ZERO rows
        page_before = self.store.list_messages(
            wechat_identity_uuid="ident-canonical-ui",
            chat_id="chat-ui@chatroom",
        )
        self.assertEqual(len(page_before), 0)

        # 3. Perform repair
        canonical_messages = [
            make_msg(
                message_id=f"msg-ui-{i}",
                chat_id="chat-ui@chatroom",
                instance_uuid="inst-canonical-ui",
                wechat_identity_uuid="ident-canonical-ui",
                text=f"Chat message {i}",
            )
            for i in range(5)
        ]
        accounts = [{
            "account_id": "acc-1",
            "wechat_identity_uuid": "ident-canonical-ui",
            "instance_uuid": "inst-canonical-ui",
        }]
        core = MockCoreClient(canonical_messages, accounts=accounts)

        repair_res = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertTrue(repair_res["ok"])
        self.assertTrue(repair_res["swapped"])

        # 4. Verify that AFTER repair, UI identity query returns all 5 rows
        page_after = self.store.list_messages(
            wechat_identity_uuid="ident-canonical-ui",
            chat_id="chat-ui@chatroom",
        )
        self.assertEqual(len(page_after), 5)
        self.assertEqual(page_after[0]["wechat_identity_uuid"], "ident-canonical-ui")
        self.assertEqual(page_after[0]["instance_uuid"], "inst-canonical-ui")

    def test_safety_reject_missing_canonical_ownership(self) -> None:
        """Identity enrichment mode rejects missing canonical ownership in shadow."""
        msg_active = make_msg(message_id="msg-001", instance_uuid="", wechat_identity_uuid="")
        self._seed_active_messages([msg_active])

        # Shadow also has blank canonical ownership
        msg_shadow = make_msg(message_id="msg-001", instance_uuid="", wechat_identity_uuid="")
        core = MockCoreClient([msg_shadow])

        res = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertFalse(res["ok"])
        self.assertFalse(res["swapped"])
        self.assertFalse(res["parity"]["parity_ok"])
        self.assertEqual(res["parity"]["mismatched_count"], 1)

    def test_safety_reject_account_cross_contamination(self) -> None:
        """Identity enrichment mode rejects account/identity cross-contamination."""
        msg_active = make_msg(account_id="acc-1", message_id="msg-001", instance_uuid="", wechat_identity_uuid="")
        self._seed_active_messages([msg_active])

        # Core accounts define acc-1 as ident-alpha, but message returns ident-beta!
        msg_shadow = make_msg(
            account_id="acc-1",
            message_id="msg-001",
            instance_uuid="inst-alpha",
            wechat_identity_uuid="ident-beta",
        )
        accounts = [
            {"account_id": "acc-1", "wechat_identity_uuid": "ident-alpha", "instance_uuid": "inst-alpha"},
            {"account_id": "acc-2", "wechat_identity_uuid": "ident-beta", "instance_uuid": "inst-beta"},
        ]
        core = MockCoreClient([msg_shadow], accounts=accounts)

        res = rebuild_projection(self.store, core, swap=True, mode="identity_enrichment")
        self.assertFalse(res["ok"])
        self.assertFalse(res["swapped"])
        self.assertFalse(res["parity"]["parity_ok"])
        self.assertEqual(res["parity"]["mismatched_count"], 1)


if __name__ == "__main__":
    unittest.main()
