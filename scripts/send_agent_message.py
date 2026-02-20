#!/usr/bin/env python3
"""
send_agent_message.py — Validated send path for inter-agent messages.

Usage: python3 send_agent_message.py --from forge --to ghost --content "..."

Validates before INSERT. Blocked messages stored with blocked=1.
Returns: {sent: bool, blocked: bool, requires_review: bool, id: str}
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts/ is on path for import
sys.path.insert(0, str(Path(__file__).parent))
from validate_agent_message import validate_message, _get_db, _now_iso, _log_security_audit

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")

# Protected agent IDs — cannot be used as from_agent
PROTECTED_AGENT_IDS = {"vector", "__shared__"}

# ── Rate limiting constants ────────────────────────────────────────────────────
MAX_MESSAGES_PER_AGENT_PER_HOUR = 10   # per sender → any recipient (general limit)
MAX_MESSAGES_TO_VECTOR_PER_HOUR = 5    # stricter limit for VECTOR inbox


def check_rate_limit(from_agent: str, to_agent: str, conn: sqlite3.Connection) -> tuple[bool, int]:
    """
    Check if a send is within rate limits.

    Returns (allowed: bool, limit: int)
      allowed=True  → send is permitted
      allowed=False → rate limit exceeded, do NOT insert
    """
    limit = (
        MAX_MESSAGES_TO_VECTOR_PER_HOUR
        if to_agent.lower() == 'vector'
        else MAX_MESSAGES_PER_AGENT_PER_HOUR
    )
    count = conn.execute(
        """SELECT COUNT(*) FROM agent_messages
           WHERE from_agent_id=? AND to_agent_id=?
           AND created_at > datetime('now', '-1 hour')
           AND blocked=0""",
        (from_agent, to_agent),
    ).fetchone()[0]
    return count < limit, limit


def send_message(from_agent: str, to_agent: str, content: str) -> dict:
    """
    Validate and send an inter-agent message.

    Returns: {sent: bool, blocked: bool, requires_review: bool, id: str, message: str}
    """
    # ── Guard: protected namespace ────────────────────────────────────────────
    if from_agent.lower() in PROTECTED_AGENT_IDS:
        return {
            "sent": False,
            "blocked": True,
            "rate_limited": False,
            "requires_review": False,
            "id": "",
            "message": f"ERROR: from_agent '{from_agent}' is in the protected namespace. "
                       f"Protected IDs cannot send agent messages: {sorted(PROTECTED_AGENT_IDS)}",
        }

    # ── Guard: self-message ───────────────────────────────────────────────────
    if from_agent.lower() == to_agent.lower():
        return {
            "sent": False,
            "blocked": True,
            "rate_limited": False,
            "requires_review": False,
            "id": "",
            "message": f"ERROR: self-message not allowed (from=to='{from_agent}')",
        }

    # ── Run validator FIRST ───────────────────────────────────────────────────
    # Validator runs BEFORE rate limit check.
    # Reason: validator-blocked attacks (Tier1) must return blocked=True, not rate_limited=True.
    # Rate limiting applies only to messages that pass the validator — protecting the cognitive
    # pipeline from valid-looking floods, not re-labeling attacks as rate-limit violations.
    validation = validate_message(content, from_agent, to_agent)

    msg_id = str(uuid.uuid4())
    now = _now_iso()

    conn = _get_db()

    if validation["blocked"]:
        # Store blocked message for audit trail (does NOT count toward rate limit)
        validator_log = json.dumps(validation["log"])
        blocked_reason = "; ".join(
            v for v in validation["violations"] if v.startswith("TIER1")
        )
        conn.execute(
            """
            INSERT INTO agent_messages
              (id, from_agent_id, to_agent_id, content, validated, blocked,
               blocked_reason, sanitized_content, requires_review, validator_log,
               created_at, read_at, read_by)
            VALUES (?, ?, ?, ?, 1, 1, ?, ?, 0, ?, ?, NULL, NULL)
            """,
            (
                msg_id, from_agent, to_agent, content,
                blocked_reason, "",
                validator_log, now,
            ),
        )
        detail = json.dumps({
            "msg_id": msg_id,
            "from": from_agent,
            "to": to_agent,
            "violations": validation["violations"],
            "content_preview": content[:200],
        })
        _log_security_audit(
            conn,
            agent=from_agent,
            violation_type="TIER1_SEND_BLOCKED",
            detail=detail,
            severity="CRITICAL",
            response_taken=f"STORED_BLOCKED msg_id={msg_id}",
        )
        conn.commit()
        conn.close()

        return {
            "sent": False,
            "blocked": True,
            "rate_limited": False,
            "requires_review": False,
            "id": msg_id,
            "message": f"Message blocked by validator. Violations: {validation['violations']}",
        }

    # ── Guard: rate limit check (only for validator-approved messages) ────────
    allowed_by_rate, rl_limit = check_rate_limit(from_agent, to_agent, conn)
    if not allowed_by_rate:
        conn.close()
        return {
            "sent": False,
            "blocked": False,
            "rate_limited": True,
            "requires_review": False,
            "id": "",
            "error": f"Rate limit: max {rl_limit} messages per hour to {to_agent}",
            "message": f"Rate limit: max {rl_limit} messages per hour to {to_agent}",
        }

    # ── Allowed: INSERT with sanitized content ────────────────────────────────
    # (All validation["blocked"] cases are handled above, before rate limit.)
    sanitized = validation["sanitized_content"]
    requires_review = validation["requires_review"]
    validator_log = json.dumps(validation["log"])

    conn.execute(
        """
        INSERT INTO agent_messages
          (id, from_agent_id, to_agent_id, content, validated, blocked,
           blocked_reason, sanitized_content, requires_review, validator_log,
           created_at, read_at, read_by)
        VALUES (?, ?, ?, ?, 1, 0, NULL, ?, ?, ?, ?, NULL, NULL)
        """,
        (
            msg_id, from_agent, to_agent, content,
            sanitized, 1 if requires_review else 0,
            validator_log, now,
        ),
    )
    conn.commit()
    conn.close()

    return {
        "sent": True,
        "blocked": False,
        "rate_limited": False,
        "requires_review": requires_review,
        "id": msg_id,
        "message": "Message sent successfully"
        + (" [flagged for review]" if requires_review else ""),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send a validated inter-agent message")
    parser.add_argument("--from", dest="from_agent", required=True, help="Sending agent ID")
    parser.add_argument("--to", dest="to_agent", required=True, help="Receiving agent ID")
    parser.add_argument("--content", required=True, help="Message content")
    args = parser.parse_args()

    result = send_message(args.from_agent, args.to_agent, args.content)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["sent"] else 1)
