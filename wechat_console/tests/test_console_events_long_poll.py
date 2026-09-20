"""Tests for Console Core Events long polling and real-time wake-up."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from wechat_console.app import ConsoleService
from wechat_console.core_client import CoreClient
from wechat_console.store import ConsoleStore


class ConsoleEventsLongPollTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.mock_core = MagicMock(spec=CoreClient)
        self.mock_core.base_url = "http://mock-core:8080"
        self.mock_core.health.return_value = {"ok": True, "supported": True}
        self.service = ConsoleService(
            core_url="http://mock-core:8080",
            db_path=self.root / "console.sqlite",
            archive_dir=self.root / "archives",
            consumer_id="console-test",
        )
        self.service.core = self.mock_core
        self.store = self.service.store

    def tearDown(self) -> None:
        self.service.stop()
        self.tmp_dir.cleanup()

    def test_sync_events_once_passes_timeout_and_notifies_waiter(self) -> None:
        # Mock poll_events returning one message event
        self.mock_core.poll_events.return_value = {
            "events": [
                {
                    "event_id": "ev-1",
                    "cursor": "100",
                    "account_id": "acc-1",
                    "event_type": "message.created",
                    "occurred_at": "2026-03-30T10:00:00Z",
                    "payload": {
                        "message": {
                            "account_id": "acc-1",
                            "message_id": "msg-1",
                            "chat_id": "chat-1",
                            "type": "text",
                            "text": "Realtime hello",
                            "created_at": "2026-03-30T10:00:00Z",
                        }
                    },
                }
            ],
            "next_cursor": "100",
            "has_more": False,
        }
        self.mock_core.ack_events.return_value = {"acked_count": 1}

        # Thread that waits for event on poll_console_events
        result_holder: list[dict] = []

        def wait_worker() -> None:
            events = self.service.poll_console_events(since="0", timeout=5.0)
            result_holder.extend(events)

        t = threading.Thread(target=wait_worker)
        t.start()

        # Wait a fraction to ensure wait_worker is waiting
        time.sleep(0.05)

        # Call sync_events_once with timeout=20
        res = self.service.sync_events_once(max_pages=1, timeout=20)
        self.assertEqual(res["events"], 1)

        # The waiting thread should wake up immediately and receive the event
        t.join(timeout=2.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(len(result_holder), 1)
        self.assertEqual(result_holder[0]["event_id"], "ev-1")
        self.assertEqual(result_holder[0]["event_type"], "message.created")

        # Verify poll_events was called with timeout=20
        self.mock_core.poll_events.assert_called_with(
            after="",
            limit=200,
            consumer_id="console-test",
            timeout=20,
        )


if __name__ == "__main__":
    unittest.main()
