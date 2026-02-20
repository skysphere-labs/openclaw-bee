#!/usr/bin/env python3
"""
update_beliefs.py — Parse PM task output JSON and write belief_updates to DB.
VECTOR calls this after every PM task completion.

Usage:
    python3 update_beliefs.py --agent forge --output '{"belief_updates":[...],"memory_operations":[...]}'
    python3 update_beliefs.py --agent forge --file /path/to/pm_output.json
"""
import argparse, json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path

DB = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def update_beliefs(agent_id: str, output_json: dict):
    conn = sqlite3.connect(DB)
    belief_updates = output_json.get("belief_updates", [])
    memory_ops = output_json.get("memory_operations", [])

    stored = 0
    for b in belief_updates:
        content = str(b.get("content", "")).strip()
        if not content or len(content) < 10 or len(content) > 500:
            continue
        category = b.get("category", "fact")
        if category not in ("identity", "goal", "preference", "decision", "fact"):
            category = "fact"
        confidence = float(b.get("confidence", 0.65))
        confidence = max(0.5, min(1.0, confidence))
        importance = float(b.get("importance", 5.0))
        importance = max(1.0, min(10.0, importance))
        action_impl = str(b.get("action_implication", ""))[:500]
        evidence_for = str(b.get("evidence_for", ""))[:500]
        evidence_against = str(b.get("evidence_against", ""))[:500]

        bid = f"pm-{agent_id[:8]}-{uuid.uuid4().hex[:8]}"
        conn.execute("""
            INSERT OR IGNORE INTO beliefs
            (id, content, confidence, category, status, agent_id, source, importance,
             action_implication, evidence_for, evidence_against, created_at, updated_at)
            VALUES (?,?,?,?,'provisional',?,?,?,?,?,?,?,?)
        """, (bid, content, confidence, category, agent_id,
              f"pm_task:{agent_id}", importance, action_impl,
              evidence_for, evidence_against, now_iso(), now_iso()))
        stored += 1

    # memory_operations
    for op in memory_ops:
        operation = op.get("op", "store")
        content = str(op.get("content", "")).strip()
        importance = float(op.get("importance", 5.0))
        if operation == "store" and content:
            mid = uuid.uuid4().hex[:8]
            conn.execute("""
                INSERT OR IGNORE INTO memories (id, agent_id, content, importance, source, created_at)
                VALUES (?,?,?,?,?,?)
            """, (mid, agent_id, content, importance, f"pm_memory_op:{agent_id}", now_iso()))
        elif operation == "archive":
            # Archive by content match for this agent
            conn.execute("UPDATE beliefs SET status='archived' WHERE agent_id=? AND content=?",
                         (agent_id, content))

    # Phase 2B: knowledge_gaps from PM output
    gaps_stored = 0
    for gap in output_json.get("knowledge_gaps", []):
        domain = str(gap.get("domain", "unknown"))[:100]
        description = str(gap.get("description", "")).strip()
        if not description or len(description) < 10:
            continue
        importance = float(gap.get("importance", 5.0))
        importance = max(1.0, min(10.0, importance))
        gid = uuid.uuid4().hex[:8]
        conn.execute("""INSERT OR IGNORE INTO knowledge_gaps (id, agent_id, domain, description, importance)
                       VALUES (?,?,?,?,?)""",
                    (gid, agent_id, domain, description, importance))
        gaps_stored += 1

    conn.execute("INSERT INTO audit_log (agent, action, detail) VALUES (?,?,?)",
                 (agent_id, "belief_update", json.dumps({"stored": stored, "memory_ops": len(memory_ops), "gaps": gaps_stored})))
    conn.commit()
    conn.close()
    print(f"update_beliefs: stored={stored} memory_ops={len(memory_ops)} gaps={gaps_stored} for agent={agent_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--output", default="", help="JSON string")
    parser.add_argument("--file", default="", help="Path to JSON file")
    args = parser.parse_args()

    if args.file:
        data = json.loads(Path(args.file).read_text())
    elif args.output:
        data = json.loads(args.output)
    else:
        print("ERROR: --output or --file required")
        exit(1)

    update_beliefs(args.agent, data)
