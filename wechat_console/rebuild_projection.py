"""Console message projection rebuild maintenance tool (RC.14).

Rebuilds Console.message_projection strictly from canonical Core query APIs
into a shadow table, verifies field-level parity, and transactionally swaps.
Does NOT consume Console.core_events.
Preserves all durable user data (saved messages, saved media, send projection, logs).

Fail-closed safety guarantees:
- Parity gate requires identical key sets, zero missing rows, zero extra rows,
  zero field mismatches, and identical row counts.
- Any parity failure aborts swap and leaves active projection untouched.
- Callers never receive ok=True on refused parity gate.
- Optional explicit identity-enrichment mode allows only blank -> canonical
  ownership repair; rejects conflicts and cross-contamination.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from .core_client import CoreClient
from .store import ConsoleStore, utc_now


def create_shadow_table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS message_projection_shadow")
    conn.execute(
        """
        CREATE TABLE message_projection_shadow (
            account_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            instance_uuid TEXT NOT NULL DEFAULT '',
            wechat_identity_uuid TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            direction TEXT NOT NULL,
            author_json TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            media_id TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT '',
            target_message_id TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            removed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(account_id, message_id)
        )
        """
    )


def insert_shadow_message(conn: sqlite3.Connection, msg: dict[str, Any], scoped_account_id: str) -> None:
    now = utc_now()
    account_id = str(msg.get("account_id") or scoped_account_id).strip()
    message_id = str(msg.get("message_id") or "").strip()
    chat_id = str(msg.get("chat_id") or "").strip()
    if not account_id or not message_id or not chat_id:
        return

    author = msg.get("author") if isinstance(msg.get("author"), dict) else {}
    conn.execute(
        """
        INSERT INTO message_projection_shadow (
            account_id, message_id, chat_id, instance_uuid, wechat_identity_uuid,
            type, created_at, direction,
            author_json, text, media_id, filename, mime_type, target_message_id,
            payload_json, removed, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
        ON CONFLICT(account_id, message_id) DO UPDATE SET
            chat_id=excluded.chat_id,
            instance_uuid=excluded.instance_uuid,
            wechat_identity_uuid=excluded.wechat_identity_uuid,
            type=excluded.type,
            created_at=excluded.created_at,
            direction=excluded.direction,
            author_json=excluded.author_json,
            text=excluded.text,
            media_id=excluded.media_id,
            filename=excluded.filename,
            mime_type=excluded.mime_type,
            target_message_id=excluded.target_message_id,
            removed=excluded.removed,
            updated_at=excluded.updated_at
        """,
        (
            account_id,
            message_id,
            chat_id,
            str(msg.get("instance_uuid") or ""),
            str(msg.get("wechat_identity_uuid") or ""),
            str(msg.get("type") or "unsupported"),
            str(msg.get("created_at") or now),
            str(msg.get("direction") or "incoming"),
            json.dumps(author, ensure_ascii=False, sort_keys=True),
            str(msg.get("text") or ""),
            str(msg.get("media_id") or ""),
            str(msg.get("filename") or ""),
            str(msg.get("mime_type") or ""),
            str(msg.get("target_message_id") or ""),
            int(bool(msg.get("removed", False))),
            now,
        ),
    )


def swap_shadow_projection(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DROP TABLE IF EXISTS message_projection_old")
        conn.execute("ALTER TABLE message_projection RENAME TO message_projection_old")
        conn.execute("ALTER TABLE message_projection_shadow RENAME TO message_projection")
        conn.execute("DROP TABLE message_projection_old")

        # Ensure indexes
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_message_projection_chat_time "
            "ON message_projection(account_id, chat_id, created_at DESC, message_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_message_projection_type "
            "ON message_projection(account_id, chat_id, type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_message_projection_identity_chat_time "
            "ON message_projection(wechat_identity_uuid, chat_id, created_at DESC, message_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_message_projection_instance_chat_time "
            "ON message_projection(instance_uuid, chat_id, created_at DESC, message_id DESC)"
        )


def verify_projection_parity(
    conn: sqlite3.Connection,
    *,
    mode: str = "strict",
    canonical_accounts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify parity between message_projection and shadow table before swap.

    Modes:
      - 'strict': exact match on all keys and all fields (including instance_uuid
        and wechat_identity_uuid). Any difference rejects swap.
      - 'identity_enrichment': explicit maintenance mode allowing only:
          active instance_uuid/wechat_identity_uuid = blank
          shadow instance_uuid/wechat_identity_uuid = canonical nonblank Core value
        All other fields and message keys must match identically.
    """
    if mode not in ("strict", "identity_enrichment"):
        raise ValueError(f"Unknown parity mode: {mode}")

    active_rows = conn.execute(
        "SELECT account_id, message_id, chat_id, type, created_at, direction, "
        "author_json, text, media_id, filename, mime_type, target_message_id, "
        "instance_uuid, wechat_identity_uuid, removed "
        "FROM message_projection ORDER BY account_id, message_id"
    ).fetchall()

    shadow_rows = conn.execute(
        "SELECT account_id, message_id, chat_id, type, created_at, direction, "
        "author_json, text, media_id, filename, mime_type, target_message_id, "
        "instance_uuid, wechat_identity_uuid, removed "
        "FROM message_projection_shadow ORDER BY account_id, message_id"
    ).fetchall()

    active_map = {(r["account_id"], r["message_id"]): dict(r) for r in active_rows}
    shadow_map = {(r["account_id"], r["message_id"]): dict(r) for r in shadow_rows}

    matched = 0
    enriched = 0
    mismatched: list[dict[str, Any]] = []
    missing_in_shadow: list[tuple[str, str]] = []
    extra_in_shadow: list[tuple[str, str]] = []

    # Map canonical accounts for cross-contamination checking
    account_canonical_identities: dict[str, str] = {}
    account_canonical_instances: dict[str, str] = {}
    if canonical_accounts:
        for acc in canonical_accounts:
            aid = str(acc.get("account_id") or "").strip()
            if aid:
                if acc.get("wechat_identity_uuid"):
                    account_canonical_identities[aid] = str(acc["wechat_identity_uuid"]).strip()
                if acc.get("instance_uuid"):
                    account_canonical_instances[aid] = str(acc["instance_uuid"]).strip()

    # Core message fields that MUST match identically in all modes
    content_fields = (
        "account_id",
        "message_id",
        "chat_id",
        "type",
        "created_at",
        "direction",
        "text",
        "media_id",
        "filename",
        "mime_type",
        "target_message_id",
        "removed",
    )

    for key, act in active_map.items():
        if key not in shadow_map:
            missing_in_shadow.append(key)
        else:
            shd = shadow_map[key]
            diffs: dict[str, Any] = {}

            # 1. Compare content fields
            for field in content_fields:
                if act[field] != shd[field]:
                    diffs[field] = {"active": act[field], "shadow": shd[field]}

            # 2. Compare author_json normalized
            act_author = act.get("author_json") or "{}"
            shd_author = shd.get("author_json") or "{}"
            try:
                if json.loads(act_author) != json.loads(shd_author):
                    diffs["author_json"] = {"active": act_author, "shadow": shd_author}
            except Exception:
                if act_author != shd_author:
                    diffs["author_json"] = {"active": act_author, "shadow": shd_author}

            # 3. Compare identity ownership fields according to mode
            act_inst = str(act.get("instance_uuid") or "").strip()
            act_ident = str(act.get("wechat_identity_uuid") or "").strip()
            shd_inst = str(shd.get("instance_uuid") or "").strip()
            shd_ident = str(shd.get("wechat_identity_uuid") or "").strip()

            if mode == "strict":
                if act_inst != shd_inst:
                    diffs["instance_uuid"] = {"active": act_inst, "shadow": shd_inst}
                if act_ident != shd_ident:
                    diffs["wechat_identity_uuid"] = {"active": act_ident, "shadow": shd_ident}
            elif mode == "identity_enrichment":
                # Only blank -> canonical enrichment allowed.
                ident_matches = (act_ident == shd_ident and act_inst == shd_inst)
                blank_enrichment = (
                    (not act_ident and bool(shd_ident))
                    or (not act_inst and bool(shd_inst))
                )

                if not ident_matches and not blank_enrichment:
                    # Nonblank conflict or regression to blank
                    if act_ident and shd_ident != act_ident:
                        diffs["wechat_identity_uuid"] = {
                            "error": "identity_conflict",
                            "active": act_ident,
                            "shadow": shd_ident,
                        }
                    if act_inst and shd_inst != act_inst:
                        diffs["instance_uuid"] = {
                            "error": "instance_conflict",
                            "active": act_inst,
                            "shadow": shd_inst,
                        }

                # Shadow must have canonical nonblank ownership
                if not shd_ident:
                    diffs["wechat_identity_uuid"] = {
                        "error": "missing_canonical_ownership",
                        "active": act_ident,
                        "shadow": shd_ident,
                    }
                if not shd_inst:
                    diffs["instance_uuid"] = {
                        "error": "missing_canonical_ownership",
                        "active": act_inst,
                        "shadow": shd_inst,
                    }

                # Check account/identity cross-contamination if canonical accounts provided
                aid = act["account_id"]
                if aid in account_canonical_identities:
                    canonical_id = account_canonical_identities[aid]
                    if canonical_id and shd_ident != canonical_id:
                        diffs["wechat_identity_uuid"] = {
                            "error": "account_identity_cross_contamination",
                            "account_id": aid,
                            "shadow": shd_ident,
                            "expected_canonical": canonical_id,
                        }
                if aid in account_canonical_instances:
                    canonical_inst = account_canonical_instances[aid]
                    if canonical_inst and shd_inst != canonical_inst:
                        diffs["instance_uuid"] = {
                            "error": "account_instance_cross_contamination",
                            "account_id": aid,
                            "shadow": shd_inst,
                            "expected_canonical": canonical_inst,
                        }

                if not diffs and (not act_ident or not act_inst) and (shd_ident or shd_inst):
                    enriched += 1

            if diffs:
                mismatched.append({"key": key, "diffs": diffs})
            else:
                matched += 1

    for key in shadow_map:
        if key not in active_map:
            extra_in_shadow.append(key)

    # Fail closed: parity requires zero missing, zero extra, zero mismatched, and equal counts
    parity_ok = (
        len(missing_in_shadow) == 0
        and len(extra_in_shadow) == 0
        and len(mismatched) == 0
        and len(active_rows) == len(shadow_rows)
    )

    return {
        "parity_ok": parity_ok,
        "mode": mode,
        "active_count": len(active_rows),
        "shadow_count": len(shadow_rows),
        "matched_count": matched,
        "mismatched_count": len(mismatched),
        "enriched_count": enriched,
        "missing_in_shadow": missing_in_shadow[:10],
        "extra_in_shadow": extra_in_shadow[:10],
        "mismatched": mismatched[:10],
    }


def rebuild_projection(
    store: ConsoleStore,
    core_client: CoreClient,
    *,
    swap: bool = True,
    mode: str = "strict",
) -> dict[str, Any]:
    """Execute complete projection rebuild workflow from Core canonical APIs.

    Enforces fail-closed parity gating:
    - Never swaps on parity failure.
    - Leaves active projection untouched on parity failure.
    - Never returns ok=True after a refused parity gate.
    """
    if mode not in ("strict", "identity_enrichment"):
        raise ValueError(f"Unknown parity mode: {mode}")

    with store.connect() as conn:
        create_shadow_table(conn)

    # 1. Discover accounts from Core
    accounts_data = core_client.accounts()
    accounts = accounts_data.get("accounts") or []

    total_ingested = 0

    with store.connect() as conn:
        for acc in accounts:
            account_id = str(acc.get("account_id") or "")
            if not account_id:
                continue
            # Query chats for this account
            try:
                chats_data = core_client.chats(account_id)
                chats = chats_data.get("chats") or []
            except Exception:
                chats = []

            for chat in chats:
                chat_id = str(chat.get("chat_id") or "")
                if not chat_id:
                    continue
                cursor = ""
                has_more = True
                while has_more:
                    try:
                        page = core_client.messages(account_id, chat_id, cursor=cursor, limit=100)
                    except Exception:
                        break
                    msgs = page.get("messages") or []
                    for msg in msgs:
                        insert_shadow_message(conn, msg, account_id)
                        total_ingested += 1
                    has_more = bool(page.get("has_more"))
                    cursor = str(page.get("next_cursor") or "")
                    if not cursor:
                        break

    # 2. Check parity (fail-closed gate)
    with store.connect() as conn:
        parity = verify_projection_parity(conn, mode=mode, canonical_accounts=accounts)

        if not parity["parity_ok"]:
            # Parity failed - FAIL CLOSED.
            # Do NOT swap. Active projection remains untouched.
            return {
                "ok": False,
                "error": "Parity check failed before swap",
                "mode": mode,
                "total_messages_rebuilt": total_ingested,
                "parity": parity,
                "swapped": False,
            }

        # 3. Swap only when parity check passed AND swap=True
        swapped = False
        if swap:
            swap_shadow_projection(conn)
            swapped = True

    return {
        "ok": True,
        "mode": mode,
        "total_messages_rebuilt": total_ingested,
        "parity": parity,
        "swapped": swapped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild Console message projection from Core canonical APIs")
    parser.add_argument("--db", required=True, help="Path to Console SQLite database")
    parser.add_argument("--core-url", required=True, help="Base URL of Core service")
    parser.add_argument("--no-swap", action="store_true", help="Do not swap shadow table into active table")
    parser.add_argument(
        "--mode",
        choices=["strict", "identity_enrichment"],
        default="strict",
        help="Rebuild parity verification mode (default: strict)",
    )
    parser.add_argument(
        "--allow-identity-enrichment",
        action="store_true",
        help="Opt-in to identity-enrichment repair mode (equivalent to --mode identity_enrichment)",
    )
    args = parser.parse_args()

    mode = "identity_enrichment" if args.allow_identity_enrichment else args.mode
    store = ConsoleStore(Path(args.db), Path(args.db).parent / "archive")
    core_client = CoreClient(args.core_url)
    result = rebuild_projection(store, core_client, swap=not args.no_swap, mode=mode)
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
