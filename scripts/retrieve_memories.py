#!/usr/bin/env python3
"""Retrieve memories for an agent using ACT-R activation scoring."""
import argparse, sqlite3, math
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument("--agent", required=True)
parser.add_argument("--query", default="")
parser.add_argument("--queries", nargs='*', default=None)
parser.add_argument("--limit", type=int, default=5)
args = parser.parse_args()

DB = "/Users/acevashisth/.openclaw/workspace/state/vector.db"
now = datetime.now(timezone.utc)

conn = sqlite3.connect(DB)
rows = conn.execute(
    """SELECT id, content, importance, decay_rate, access_count, last_accessed, activation_score
       FROM memories WHERE agent_id IN (?, '__shared__')""",
    (args.agent,),
).fetchall()

def act_r_score(importance, decay_rate, access_count, last_accessed):
    recency = 0.5
    if last_accessed:
        try:
            delta = (now - datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))).total_seconds()
            recency = math.exp(-decay_rate * delta / 86400)
        except Exception:
            pass
    freq = math.log(max(access_count, 1) + 1)
    return importance * recency * freq

scored = [(act_r_score(*r[2:6]), r) for r in rows]
scored.sort(reverse=True)

# Multi-keyword filter has precedence when provided
if args.queries is not None and len(args.queries) > 0:
    query_terms = [q.lower() for q in args.queries if q and q.strip()]
    filtered = [(s, r) for s, r in scored if any(q in r[1].lower() for q in query_terms)]
    scored = filtered
# Backward-compatible single query behavior
elif args.query:
    tokens = args.query.lower().split()
    filtered = [(s, r) for s, r in scored if any(t in r[1].lower() for t in tokens)]
    if filtered:
        scored = filtered

for score, row in scored[:args.limit]:
    print(f"[{score:.3f}] {row[1][:120]}")
conn.close()
