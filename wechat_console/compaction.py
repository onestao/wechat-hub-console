"""Console core_events deduplication and compaction maintenance primitive (RC.14).

Test/temporary maintenance only. Deletes redundant account.status events from
Console.core_events after they have already been projected, while preserving all
non-status events and leaving all projections and durable user data untouched.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .store import ConsoleStore


CHUNK_SIZE = 500


def _account_status_event_semantic(account: Any) -> dict[str, Any]:
    if not isinstance(account, dict):
        return {}
    top = {
        "account_id": str(account.get("account_id") or "").strip(),
        "display_name": str(account.get("display_name") or "").strip(),
        "state": str(account.get("state") or "").strip(),
    }
    raw_runtime = account.get("runtime") if isinstance(account.get("runtime"), dict) else {}
    runtime_semantic = {}
    for key in (
        "runtime_provider", "container_state", "status", "running", "wechat_state",
        "logged_in", "login_status", "logged_in_user", "username", "health",
        "available", "ready", "sender_enabled", "sender_driver", "error_code",
        "error_class", "error", "registered", "display",
    ):
        if key in raw_runtime:
            runtime_semantic[key] = raw_runtime[key]

    raw_sync = account.get("sync") if isinstance(account.get("sync"), dict) else {}
    sync_semantic = {}
    for key in (
        "enabled", "ok", "stale", "degraded", "status", "health",
        "error_code", "error_class", "error",
    ):
        if key in raw_sync:
            sync_semantic[key] = raw_sync[key]

    return {"top": top, "runtime": runtime_semantic, "sync": sync_semantic}


@contextmanager
def _open_connection(target: ConsoleStore | sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    if isinstance(target, ConsoleStore):
        with target.connect() as conn:
            yield conn
    else:
        yield target


def plan_console_core_events_compaction(target: ConsoleStore | sqlite3.Connection) -> dict[str, Any]:
    """Dry-run compaction planner for Console core_events table."""
    with _open_connection(target) as conn:
        rows = conn.execute(
            "SELECT cursor, event_id, account_id, occurred_at, payload_json "
            "FROM core_events WHERE event_type='account.status' ORDER BY account_id, CAST(cursor AS INTEGER) ASC, rowid ASC"
        ).fetchall()

        events_by_account: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            acc = row["account_id"]
            if acc not in events_by_account:
                events_by_account[acc] = []
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}
            raw_cursor = row["cursor"]
            cursor_val = int(raw_cursor) if str(raw_cursor).isdigit() else 0
            events_by_account[acc].append(
                {
                    "cursor": cursor_val,
                    "event_id": str(row["event_id"]),
                    "account_id": acc,
                    "occurred_at": str(row["occurred_at"]),
                    "payload": payload,
                }
            )

        candidate_event_ids: list[str] = []
        candidate_cursors: list[int] = []
        preserved_first = 0
        preserved_transition = 0
        preserved_newest = 0

        for acc, ev_list in events_by_account.items():
            if not ev_list:
                continue
            if len(ev_list) == 1:
                preserved_first += 1
                continue

            first_ev = ev_list[0]
            preserved_first += 1
            prev_semantic = _account_status_event_semantic(first_ev["payload"].get("account"))

            for ev in ev_list[1:-1]:
                curr_semantic = _account_status_event_semantic(ev["payload"].get("account"))
                if curr_semantic != prev_semantic:
                    preserved_transition += 1
                    prev_semantic = curr_semantic
                else:
                    candidate_event_ids.append(ev["event_id"])
                    candidate_cursors.append(ev["cursor"])

            preserved_newest += 1

        min_cursor = min(candidate_cursors) if candidate_cursors else 0
        max_cursor = max(candidate_cursors) if candidate_cursors else 0

        return {
            "total_status_events": len(rows),
            "redundant_candidate_rows": len(candidate_event_ids),
            "preserved_first_rows": preserved_first,
            "preserved_transition_rows": preserved_transition,
            "preserved_newest_rows": preserved_newest,
            "total_preserved_rows": len(rows) - len(candidate_event_ids),
            "candidate_event_ids": candidate_event_ids,
            "candidate_cursor_range": {"min_cursor": min_cursor, "max_cursor": max_cursor},
        }


def apply_console_core_events_compaction(
    target: ConsoleStore | sqlite3.Connection,
    plan: dict[str, Any],
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Apply removal of redundant status events from Console.core_events.

    Requires confirmed=True. For test/temporary database use only.
    """
    if not confirmed:
        raise ValueError("Compaction apply requires explicit confirmed=True parameter")

    candidate_ids = plan.get("candidate_event_ids") or []
    if not candidate_ids:
        return {"applied": True, "deleted_events": 0}

    with _open_connection(target) as conn:
        deleted = 0
        for i in range(0, len(candidate_ids), CHUNK_SIZE):
            chunk = candidate_ids[i : i + CHUNK_SIZE]
            ph = ",".join("?" for _ in chunk)
            cur = conn.execute(f"DELETE FROM core_events WHERE event_id IN ({ph})", tuple(chunk))
            deleted += cur.rowcount
        conn.commit()
        return {"applied": True, "deleted_events": deleted}
