#!/usr/bin/env python3
"""
post_proposal.py — Validated proposal submission for the shared proposals board.

Usage: python3 post_proposal.py --agent forge --title "..." --content "..." --evidence '["belief:abc","memory:xyz"]'

Validation:
  - validate_message on title + content combined
  - evidence=[] → allowed but requires_review=1
  - author_agent_id cannot be 'vector' or '__shared__'
  - content max 2000 chars, title max 200 chars

Returns: {id, blocked, requires_review, message}
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validate_agent_message import validate_message, _get_db, _now_iso, _log_security_audit

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")

PROTECTED_AGENT_IDS = {"vector", "__shared__"}
MAX_CONTENT_LENGTH = 2000
MAX_TITLE_LENGTH = 200


def post_proposal(
    agent: str,
    title: str,
    content: str,
    evidence: list[str],
) -> dict:
    """
    Validate and post a proposal to the shared proposals board.

    Returns: {id, blocked, requires_review, message}
    """
    proposal_id = str(uuid.uuid4())
    now = _now_iso()

    # ── Guard: protected namespace ────────────────────────────────────────────
    if agent.lower() in PROTECTED_AGENT_IDS:
        return {
            "id": "",
            "blocked": True,
            "requires_review": False,
            "message": f"ERROR: agent '{agent}' is in the protected namespace "
                       f"({sorted(PROTECTED_AGENT_IDS)}). These IDs cannot author proposals.",
        }

    # ── Guard: length limits ──────────────────────────────────────────────────
    if len(content) > MAX_CONTENT_LENGTH:
        return {
            "id": "",
            "blocked": True,
            "requires_review": False,
            "message": f"ERROR: content exceeds maximum length of {MAX_CONTENT_LENGTH} chars "
                       f"(got {len(content)} chars). Rejected.",
        }

    if len(title) > MAX_TITLE_LENGTH:
        return {
            "id": "",
            "blocked": True,
            "requires_review": False,
            "message": f"ERROR: title exceeds maximum length of {MAX_TITLE_LENGTH} chars "
                       f"(got {len(title)} chars). Rejected.",
        }

    # ── Run validator on title + content combined ─────────────────────────────
    combined = f"{title}\n\n{content}"
    validation = validate_message(combined, from_agent=agent, to_agent="__proposals__")

    if validation["blocked"]:
        # Log to security_audit
        conn = _get_db()
        detail = json.dumps({
            "proposal_id": proposal_id,
            "agent": agent,
            "title": title,
            "violations": validation["violations"],
            "content_preview": content[:200],
        })
        _log_security_audit(
            conn,
            agent=agent,
            violation_type="TIER1_PROPOSAL_BLOCK",
            detail=detail,
            severity="CRITICAL",
            response_taken="PROPOSAL_REJECTED_NOT_STORED",
        )
        conn.commit()
        conn.close()

        return {
            "id": "",
            "blocked": True,
            "requires_review": False,
            "message": f"Proposal BLOCKED by validator. Violations: {validation['violations']}",
        }

    # ── Determine requires_review ─────────────────────────────────────────────
    requires_review = validation["requires_review"]
    review_reasons: list[str] = []

    if not evidence:
        requires_review = True
        review_reasons.append("no evidence cited")

    validator_log_entries = list(validation["log"])
    if review_reasons:
        for reason in review_reasons:
            validator_log_entries.append(f"[REQUIRES_REVIEW] {reason}")

    # ── INSERT proposal ───────────────────────────────────────────────────────
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO proposals
          (id, author_agent_id, title, content, evidence, status,
           requires_review, blocked, blocked_reason, validator_log,
           created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'open', ?, 0, NULL, ?, ?, ?)
        """,
        (
            proposal_id,
            agent,
            title,
            content,
            json.dumps(evidence),
            1 if requires_review else 0,
            json.dumps(validator_log_entries),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()

    status_msg = "Proposal posted successfully"
    if requires_review:
        status_msg += f" [flagged for review: {', '.join(review_reasons) if review_reasons else 'validator flag'}]"

    return {
        "id": proposal_id,
        "blocked": False,
        "requires_review": requires_review,
        "message": status_msg,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post a proposal to the shared board")
    parser.add_argument("--agent", required=True, help="Authoring agent ID")
    parser.add_argument("--title", required=True, help="Proposal title")
    parser.add_argument("--content", required=True, help="Proposal content")
    parser.add_argument(
        "--evidence",
        default="[]",
        help="JSON array of evidence refs e.g. '[\"belief:abc\",\"memory:xyz\"]'",
    )
    args = parser.parse_args()

    try:
        evidence_list = json.loads(args.evidence)
    except json.JSONDecodeError as e:
        print(json.dumps({"id": "", "blocked": True, "requires_review": False,
                          "message": f"ERROR: invalid evidence JSON: {e}"}))
        sys.exit(1)

    result = post_proposal(args.agent, args.title, args.content, evidence_list)
    print(json.dumps(result, indent=2))
    sys.exit(0 if not result["blocked"] else 1)
