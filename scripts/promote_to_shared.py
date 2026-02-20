#!/usr/bin/env python3
"""Promote a belief to __shared__ scope so all PMs can access it.

Security guard: rejects beliefs with source='test' or 'Test' to prevent
test artifacts from polluting the shared namespace.
"""
import argparse, sqlite3, json, re
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument("--belief-id", required=True)
parser.add_argument("--reason", default="")
args = parser.parse_args()

DB = "/Users/acevashisth/.openclaw/workspace/state/vector.db"
conn = sqlite3.connect(DB)

row = conn.execute("SELECT id, content, agent_id, source FROM beliefs WHERE id=?", (args.belief_id,)).fetchone()
if not row:
    print(f"ERROR: belief {args.belief_id} not found")
    raise SystemExit(1)

belief_id, content, agent_id, source = row

# ── GUARD: Reject test artifacts from __shared__ namespace ──────────────────
# source field check: reject 'test', 'Test', and any casing variant
if source and re.search(r'\btest\b', source, re.IGNORECASE):
    print(f"REJECTED: belief {belief_id} has test source='{source}' — test artifacts are "
          f"forbidden in __shared__ namespace.")
    conn.execute(
        "INSERT INTO audit_log (agent, action, detail) VALUES ('vector', 'promote_shared_rejected', ?)",
        (json.dumps({"belief_id": belief_id, "reason": "test_source_guard", "source": source}),),
    )
    conn.commit()
    conn.close()
    raise SystemExit(1)

# ── GUARD: Reject content that looks like a test artifact ────────────────────
# Block if content contains 'test belief' or 'promote_to_shared' (test strings)
test_content_patterns = [
    r'\btest belief\b',
    r'promote_to_shared',
    r'\bThis is a test\b',
]
for pat in test_content_patterns:
    if re.search(pat, content, re.IGNORECASE):
        print(f"REJECTED: belief {belief_id} content matches test artifact pattern '{pat}'. "
              f"Content: {content[:100]}")
        conn.execute(
            "INSERT INTO audit_log (agent, action, detail) VALUES ('vector', 'promote_shared_rejected', ?)",
            (json.dumps({"belief_id": belief_id, "reason": "test_content_guard", "pattern": pat}),),
        )
        conn.commit()
        conn.close()
        raise SystemExit(1)

conn.execute(
    "UPDATE beliefs SET agent_id='__shared__', updated_at=? WHERE id=?",
    (datetime.now(timezone.utc).isoformat(), args.belief_id),
)
conn.execute(
    "INSERT INTO audit_log (agent, action, detail) VALUES ('vector', 'promote_shared', ?)",
    (json.dumps({"belief_id": args.belief_id, "reason": args.reason, "prev_agent": agent_id}),),
)
conn.commit()
conn.close()
print(f"Promoted {args.belief_id} to __shared__  (was: {agent_id})")
print(f"Content: {content[:100]}")
