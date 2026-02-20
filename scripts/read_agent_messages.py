#!/usr/bin/env python3
"""
read_agent_messages.py — THE ONLY sanctioned read path for agent_messages.

Usage: python3 read_agent_messages.py --to-agent ghost [--unread-only] [--limit 10]

Steps:
  1. Query agent_messages WHERE to_agent_id=? AND blocked=0
  2. Re-validate each message on read (belt-and-suspenders)
  3. If re-validation fails: update blocked=1, log to security_audit, skip
  4. Mark returned messages as read (read_at=now, read_by=to_agent_id)
  5. Return JSON array of valid messages

NEVER bypass this with raw sqlite3 queries.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validate_agent_message import validate_message, _get_db, _now_iso, _log_security_audit

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")

# Security: hard cap on messages returned per read to prevent bulk exfiltration
MAX_READ_LIMIT = 10


def read_messages(to_agent: str, unread_only: bool = False, limit: int = 10) -> list[dict]:
    # Enforce hard cap regardless of caller-supplied limit
    limit = min(limit, MAX_READ_LIMIT)
    """
    Read and re-validate messages for a given agent.

    Returns list of valid (unblocked, re-validated) messages.
    Marks them as read and updates any newly-blocked messages.
    """
    conn = _get_db()
    now = _now_iso()

    # ── Fetch candidates: blocked=0 only ──────────────────────────────────────
    query = """
        SELECT id, from_agent_id, to_agent_id, content, sanitized_content,
               requires_review, validator_log, created_at, read_at, read_by
        FROM agent_messages
        WHERE to_agent_id = ? AND blocked = 0
    """
    params: list = [to_agent]

    if unread_only:
        query += " AND read_at IS NULL"

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    valid_messages: list[dict] = []
    newly_blocked: list[str] = []

    for row in rows:
        msg_id = row["id"]
        content = row["content"]
        from_agent = row["from_agent_id"]

        # ── Belt-and-suspenders: re-validate on read ──────────────────────────
        re_validation = validate_message(content, from_agent, to_agent)

        if re_validation["blocked"]:
            # Message passed initial write-time validation but fails on re-read.
            # This can happen if validator patterns were updated after the message was stored.
            newly_blocked.append(msg_id)

            # Update DB: mark blocked
            conn.execute(
                """
                UPDATE agent_messages
                SET blocked = 1,
                    blocked_reason = ?,
                    validator_log = ?
                WHERE id = ?
                """,
                (
                    "Re-validation failed on read: "
                    + "; ".join(re_validation["violations"]),
                    json.dumps(re_validation["log"]),
                    msg_id,
                ),
            )

            # Log to security_audit
            detail = json.dumps({
                "msg_id": msg_id,
                "from": from_agent,
                "to": to_agent,
                "violations": re_validation["violations"],
                "trigger": "re_validation_on_read",
                "content_preview": content[:200],
            })
            _log_security_audit(
                conn,
                agent=from_agent,
                violation_type="TIER1_REVALIDATION_BLOCK",
                detail=detail,
                severity="CRITICAL",
                response_taken=f"BLOCKED_ON_READ msg_id={msg_id}",
            )
            # Skip this message — do not return it
            continue

        # ── Mark as read ──────────────────────────────────────────────────────
        conn.execute(
            "UPDATE agent_messages SET read_at = ?, read_by = ? WHERE id = ?",
            (now, to_agent, msg_id),
        )

        # Use sanitized_content if available, else original
        display_content = row["sanitized_content"] or content

        valid_messages.append({
            "id": msg_id,
            "from": from_agent,
            "to": to_agent,
            "content": display_content,
            "requires_review": bool(row["requires_review"]),
            "created_at": row["created_at"],
            "read_at": now,
        })

    conn.commit()
    conn.close()

    if newly_blocked:
        print(
            f"[read_agent_messages] WARNING: {len(newly_blocked)} message(s) blocked on "
            f"re-validation and quarantined: {newly_blocked}",
            file=sys.stderr,
        )

    return valid_messages


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read inter-agent messages (validated read path)"
    )
    parser.add_argument("--to-agent", required=True, dest="to_agent", help="Recipient agent ID")
    parser.add_argument(
        "--unread-only", action="store_true", default=False, help="Only return unread messages"
    )
    parser.add_argument("--limit", type=int, default=10,
                        help=f"Max messages to return (hard cap: {MAX_READ_LIMIT})")
    args = parser.parse_args()

    messages = read_messages(args.to_agent, unread_only=args.unread_only, limit=args.limit)
    print(json.dumps(messages, indent=2))
