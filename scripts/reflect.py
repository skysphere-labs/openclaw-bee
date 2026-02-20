#!/usr/bin/env python3
"""
reflect.py — Trigger belief reflection for an agent.
Synthesizes recent provisional beliefs into durable ones using Haiku.
VECTOR calls this manually or after 5 task completions.

Usage:
    python3 reflect.py --agent forge
    python3 reflect.py --agent vector --dry-run
"""
import argparse, json, sqlite3, os, uuid
from datetime import datetime, timezone
from pathlib import Path

DB = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
OPENCLAW_CONFIG = Path.home() / ".openclaw/openclaw.json"

def get_api_key():
    try:
        import json as j
        cfg = j.loads(OPENCLAW_CONFIG.read_text())
        # Walk nested dict for any key containing 'apiKey' or 'api_key'
        def find_key(obj, depth=0):
            if depth > 5: return None
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if 'apiKey' in k or 'api_key' in k:
                        if isinstance(v, str) and v.startswith('sk-'): return v
                    result = find_key(v, depth+1)
                    if result: return result
            return None
        return find_key(cfg)
    except: return None

def detect_contradictions(agent_id: str, conn: sqlite3.Connection) -> list:
    """
    Simple contradiction detection: beliefs where evidence_against is substantial
    (> 100 chars) AND confidence < 0.75 are flagged as potentially contradicted.
    For each such belief, also check if any other belief for same agent directly
    addresses the same topic (naive: shared keywords in content).
    Returns list of {id_a, id_b, reason} pairs.
    """
    candidates = conn.execute("""
        SELECT id, content, evidence_against, confidence
        FROM beliefs
        WHERE agent_id = ? AND status = 'active'
        AND evidence_against IS NOT NULL AND length(evidence_against) > 100
        AND confidence < 0.75
    """, (agent_id,)).fetchall()

    all_active = conn.execute("""
        SELECT id, content FROM beliefs
        WHERE agent_id = ? AND status = 'active'
    """, (agent_id,)).fetchall()

    stopwords = {'the','a','an','is','are','was','were','it','this','that','and','or','but','not'}
    contradictions = []
    for cid, ccontent, evid_against, conf in candidates:
        evid_words = set(evid_against.lower().split()) - stopwords
        for oid, ocontent in all_active:
            if oid == cid:
                continue
            overlap = len(set(ocontent.lower().split()) & evid_words)
            if overlap >= 3:
                contradictions.append({
                    "id_a": cid, "id_b": oid,
                    "reason": f"evidence_against overlaps with other belief ({overlap} words)"
                })
                break
    return contradictions


def reflect(agent_id: str, dry_run: bool = False):
    conn = sqlite3.connect(DB)

    # Phase 2B: contradiction detection (runs before Haiku call)
    contradictions = detect_contradictions(agent_id, conn)
    if contradictions:
        now = datetime.now(timezone.utc).isoformat()
        for pair in contradictions:
            conn.execute(
                "UPDATE beliefs SET contradicts=?, uncertainty_type='conflicting', updated_at=? WHERE id=?",
                (pair['id_b'], now, pair['id_a'])
            )
            conn.execute(
                "UPDATE beliefs SET contradicts=?, uncertainty_type='conflicting', updated_at=? WHERE id=?",
                (pair['id_a'], now, pair['id_b'])
            )
        conn.commit()
        print(f"reflect: contradiction_pairs={len(contradictions)} detected and written for agent={agent_id}")
    if dry_run and contradictions:
        print(f"reflect: DRY RUN — would flag {len(contradictions)} contradiction pairs")

    # Load pending provisional beliefs
    provisionals = conn.execute("""
        SELECT id, content, category, confidence, importance, evidence_for, created_at
        FROM beliefs
        WHERE agent_id = ? AND status = 'provisional'
        ORDER BY importance DESC, created_at DESC
        LIMIT 20
    """, (agent_id,)).fetchall()

    if len(provisionals) < 3:
        print(f"reflect: only {len(provisionals)} provisional beliefs for {agent_id} — skipping (need ≥3)")
        conn.close()
        return

    print(f"reflect: {len(provisionals)} provisional beliefs for agent={agent_id}")

    # Build reflection prompt
    belief_text = "\n".join([
        f"- [{row[2]}, conf={row[3]:.2f}, imp={row[4]:.0f}] {row[1]}"
        for row in provisionals
    ])

    prompt = f"""You are reviewing provisional beliefs for agent '{agent_id}'.

Provisional beliefs pending review:
{belief_text}

Task: Synthesize these into durable, high-quality beliefs.
Rules:
- PROMOTE: clearly durable facts, preferences, decisions (confidence stays as-is, status→active)
- MERGE: near-duplicate beliefs → combine into one stronger belief
- DISCARD: transient commands, questions, noise (mark as archived)
- CREATE: if patterns suggest a new insight not explicitly stated, create it

Respond with ONLY valid JSON:
{{"promote": ["belief_id_1", "belief_id_2"], "archive": ["belief_id_3"], "merge": [{{"ids": ["id_a", "id_b"], "merged_content": "...", "merged_confidence": 0.85}}]}}"""

    if dry_run:
        print(f"DRY RUN — would send to Haiku:\n{prompt[:500]}...")
        conn.close()
        return

    api_key = get_api_key()
    if not api_key:
        print("reflect: no API key found — cannot call Haiku")
        conn.close()
        return

    import urllib.request
    req_data = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=req_data,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        text = result.get("content", [{}])[0].get("text", "{}")
        actions = json.loads(text)
    except Exception as e:
        print(f"reflect: Haiku call failed: {e}")
        conn.close()
        return

    now = datetime.now(timezone.utc).isoformat()
    promoted = 0
    archived = 0
    merged = 0

    for bid in actions.get("promote", []):
        conn.execute("UPDATE beliefs SET status='active', updated_at=? WHERE id=? AND agent_id=?",
                    (now, bid, agent_id))
        promoted += 1

    for bid in actions.get("archive", []):
        conn.execute("UPDATE beliefs SET status='archived', updated_at=? WHERE id=? AND agent_id=?",
                    (now, bid, agent_id))
        archived += 1

        # Phase 2B: extract knowledge gaps from archived beliefs
        row = conn.execute("SELECT content, agent_id FROM beliefs WHERE id=?", (bid,)).fetchone()
        if row and len(row[0]) > 20:
            gap_triggers = ["don't know", "unclear", "unknown", "need to verify", "tbd", "not sure"]
            if any(t in row[0].lower() for t in gap_triggers):
                conn.execute("""INSERT OR IGNORE INTO knowledge_gaps
                               (id, agent_id, domain, description, importance)
                               VALUES (?,?,?,?,?)""",
                             (uuid.uuid4().hex[:8], row[1], "unknown",
                              f"Gap from archived belief: {row[0][:200]}", 4.0))

    for merge in actions.get("merge", []):
        ids = merge.get("ids", [])
        content = merge.get("merged_content", "")
        conf = float(merge.get("merged_confidence", 0.75))
        if not ids or not content: continue
        # Archive old beliefs
        for bid in ids:
            conn.execute("UPDATE beliefs SET status='archived', updated_at=? WHERE id=? AND agent_id=?",
                        (now, bid, agent_id))
        # Create merged belief
        import uuid
        new_id = f"reflect-{agent_id[:6]}-{uuid.uuid4().hex[:8]}"
        conn.execute("""INSERT INTO beliefs (id, content, confidence, category, status, agent_id, source, created_at, updated_at)
                       VALUES (?,?,?,'fact','active',?,?,?,?)""",
                    (new_id, content, conf, agent_id, f"reflection:{agent_id}", now, now))
        merged += 1

    conn.execute("INSERT INTO audit_log (agent, action, detail) VALUES (?,?,?)",
                (agent_id, "reflection", json.dumps({"promoted": promoted, "archived": archived, "merged": merged})))
    conn.commit()
    conn.close()
    print(f"reflect: promoted={promoted} archived={archived} merged={merged} for agent={agent_id}")

parser = argparse.ArgumentParser()
parser.add_argument("--agent", required=True)
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
reflect(args.agent, args.dry_run)
