import tempfile
import unittest
from pathlib import Path

from wechat_console.store import ConsoleStore


class ConsoleLongPollBacklogTest(unittest.TestCase):
    """P1 tests: Console long poll cursor delivery when backlog > 100 and clean stop."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.console_db = self.root / "console.sqlite"
        self.archive_dir = self.root / "archive"
        self.store = ConsoleStore(self.console_db, self.archive_dir)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp_dir.cleanup()

    def test_console_backlog_over_100_does_not_skip_cursor(self) -> None:
        """P1: When events backlog > 100, polling does NOT skip un-delivered cursors."""
        # Insert 150 events into console store
        events = [
            {
                "cursor": str(i),
                "event_id": f"evt_{i}",
                "event_type": "message.created",
                "account_id": "acc_1",
                "occurred_at": "2026-03-30T10:00:00Z",
                "payload": {"index": i},
            }
            for i in range(1, 151)
        ]
        self.store.ingest_events(events, next_cursor="150")

        self.assertEqual(self.store.cursor(), "150")

        # Page 1 with since="0", limit=100
        page1 = self.store.list_events_since(since="0", limit=100)
        self.assertEqual(len(page1), 100)
        self.assertEqual(str(page1[0]["cursor"]), "1")
        self.assertEqual(str(page1[-1]["cursor"]), "100")
        delivered_cursor_1 = str(page1[-1]["cursor"])

        # Page 2 from since="100"
        page2 = self.store.list_events_since(since=delivered_cursor_1, limit=100)
        self.assertEqual(len(page2), 50)
        self.assertEqual(str(page2[0]["cursor"]), "101")
        self.assertEqual(str(page2[-1]["cursor"]), "150")


if __name__ == "__main__":
    unittest.main()
