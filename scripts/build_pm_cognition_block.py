#!/usr/bin/env python3
"""build_pm_cognition_block.py — Standalone PM belief injector."""

import argparse
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_DEFAULT = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
MAX_PRIVATE = 5
MAX_SHARED = 3
MAX_CHIEF = 5


def recency_weight(created_at_str: str, decay_rate: float = 0.1) -> float:
    """Returns weight 0-1, decaying exponentially with age in days."""
    try:
        created = datetime.fromisoformat((created_at_str or "").replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - created).days
        return math.exp(-decay_rate * max(0, age_days))
    except Exception:
        return 1.0


def _resort_with_decay(rows):
    beliefs = []
    for r in rows:
        beliefs.append(
            {
                "content": r[0],
                "category": r[1],
                "confidence": float(r[2] if r[2] is not None else 0.5),
                "action_implication": r[3],
                "importance": float(r[4] if r[4] is not None else 5.0),
                "created_at": r[5] or "",
            }
        )
    beliefs.sort(
        key=lambda b: b["importance"] * recency_weight(b.get("created_at", "")),
        reverse=True,
    )
    return beliefs


def build_pm_cognition_block(db_path: Path, agent_id: str) -> str:
    conn = sqlite3.connect(str(db_path))
    private_rows = []
    shared_rows = []
    chief_rows = []

    try:
        private_rows = conn.execute(
            """SELECT content, category, confidence, action_implication, importance, created_at
               FROM beliefs
               WHERE agent_id = ? AND status = 'active'
               ORDER BY activation_score DESC, importance DESC
               LIMIT ?""",
            (agent_id, MAX_PRIVATE * 4),
        ).fetchall()
    except Exception as e:
        print(f"[build_pm_cognition_block] WARNING: private belief query failed: {e}", file=sys.stderr)

    try:
        shared_rows = conn.execute(
            """SELECT content, category, confidence, action_implication, importance, created_at
               FROM beliefs
               WHERE agent_id = '__shared__' AND status = 'active'
               ORDER BY activation_score DESC, importance DESC
               LIMIT ?""",
            (MAX_SHARED * 4,),
        ).fetchall()
    except Exception as e:
        print(f"[build_pm_cognition_block] WARNING: shared belief query failed: {e}", file=sys.stderr)

    try:
        chief_rows = conn.execute(
            """SELECT content, category, confidence, action_implication, importance, created_at
               FROM beliefs
               WHERE agent_id = 'chief' AND status = 'active'
               ORDER BY importance DESC, activation_score DESC
               LIMIT ?""",
            (MAX_CHIEF,),
        ).fetchall()
    except Exception as e:
        print(f"[build_pm_cognition_block] WARNING: chief belief query failed: {e}", file=sys.stderr)

    conn.close()

    private_beliefs = _resort_with_decay(private_rows)[:MAX_PRIVATE]
    shared_beliefs = _resort_with_decay(shared_rows)[:MAX_SHARED]
    chief_beliefs = _resort_with_decay(chief_rows)[:MAX_CHIEF]

    def format_belief(row: dict) -> str:
        base = f"- [{row['category']}, {row['confidence']:.2f}] {row['content']}"
        if row.get("action_implication") and str(row["action_implication"]).strip():
            return f"{base}\n  → {str(row['action_implication']).strip()}"
        return base

    block = "<pm-cognition>\n"
    block += "## Your beliefs (private)\n"
    if private_beliefs:
        block += "\n".join(format_belief(r) for r in private_beliefs) + "\n"
    else:
        block += "No prior beliefs for this agent — forming from scratch.\n"

    block += "\n## Shared context (from VECTOR)\n"
    if shared_beliefs:
        block += "\n".join(format_belief(r) for r in shared_beliefs) + "\n"
    else:
        block += "No shared context available.\n"

    if chief_beliefs:
        block += "\n## Chief's observed preferences (read-only — extracted from real interactions)\n"
        block += "\n".join(format_belief(r) for r in chief_beliefs) + "\n"

    block += "</pm-cognition>"
    return block


def main():
    parser = argparse.ArgumentParser(description="Build a <pm-cognition> block from active beliefs for a PM agent.")
    parser.add_argument("--agent", required=True, help="PM agent ID (e.g. forge, ghost, oracle)")
    parser.add_argument("--db", default=str(DB_DEFAULT), help="Path to vector.db")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    print(build_pm_cognition_block(db_path, args.agent))


if __name__ == "__main__":
    main()
