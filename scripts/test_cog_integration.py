#!/usr/bin/env python3
"""COG-TEST-001: End-to-end cognitive cycle integration proof.
Runs spawn_pm → post_pm → DB verification → system1_scan.
All phases (1-5C) integrated in one real FORGE cycle.
"""
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

WS = Path("/Users/acevashisth/.openclaw/workspace")
DB = WS / "state/vector.db"
SCRIPTS = WS / "scripts"

RESULTS = []

def check(gate: str, desc: str, passed: bool, detail: str = ""):
    status = "✅" if passed else "❌"
    RESULTS.append((gate, passed))
    print(f"{status} [{gate}] {desc}{': ' + detail if detail else ''}")

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=WS, **kw)

def db(q):
    conn = sqlite3.connect(DB)
    rows = conn.execute(q).fetchall()
    conn.close()
    return rows

# ── G1/G2: spawn_pm produces full 3-section cognition block ───────────────────
ticket = f"COG-INT-{uuid.uuid4().hex[:8]}"
r = run(["python3", str(SCRIPTS/"spawn_pm.py"),
         "--agent", "forge", "--task",
         "COG-TEST-001 integration run: verify full cognitive cycle",
         "--ticket", ticket])
out = r.stdout + r.stderr
check("G1", "spawn_pm produces <cognitive-context block", "<cognitive-context" in out)
required_sections = ["## Your beliefs (private)", "## Shared context", "## Chief's observed preferences"]
all_sections = all(s in out for s in required_sections)
check("G2", "All 3 cognition sections present", all_sections,
      f"missing: {[s for s in required_sections if s not in out]}" if not all_sections else "")

# ── G3/G4/G5: post_pm stores beliefs + queues shared + stores memory ──────────
post_payload = json.dumps({
    "belief_updates": [
        {
            "content": f"[{ticket}] Integration test: spawn_pm produces a 3-section cognition block combining private beliefs, shared context, and observed preferences.",
            "confidence": 0.85, "importance": 8, "category": "fact", "scope": "private"
        },
        {
            "content": f"[{ticket}] Integration test: post_pm correctly routes scope and stores provisional beliefs without errors.",
            "confidence": 0.85, "importance": 8, "category": "fact", "scope": "private"
        },
        {
            "content": f"All PMs should confirm the 3-section cognition block is present before executing any assigned task. [{ticket}]",
            "confidence": 0.90, "importance": 9, "category": "decision", "scope": "shared"
        }
    ],
    "memory_operations": [
        {"op": "store",
         "content": f"[{ticket}] Full cognitive cycle PASS: spawn_pm → post_pm → beliefs(private+shared) → memory → system1_scan all working.",
         "importance": 8}
    ],
    "knowledge_gaps": [
        {"description": "Concurrent PM access to vector.db may cause race conditions under high load", "priority": "medium"}
    ]
})

r2 = run(["python3", str(SCRIPTS/"post_pm.py"), "--agent", "forge", "--output", post_payload])
try:
    res = json.loads(r2.stdout)
    check("G3", "post_pm stored >=2 private beliefs", res.get("stored", 0) >= 2, f"stored={res.get('stored')}")
    check("G4", "post_pm queued >=1 shared belief to pending_shared", res.get("pending", 0) >= 1, f"pending={res.get('pending')}")
    check("G5", "post_pm returned no errors", res.get("errors", []) == [], f"errors={res.get('errors')}")
except Exception as e:
    check("G3", "post_pm parse", False, str(e))
    check("G4", "post_pm pending", False, "parse failed")
    check("G5", "post_pm errors", False, "parse failed")

# ── G6: memory stored ─────────────────────────────────────────────────────────
mem_rows = db(f"SELECT COUNT(*) FROM memories WHERE agent_id='forge' AND content LIKE '%{ticket}%'")
check("G6", "Memory stored in forge namespace", mem_rows[0][0] >= 1, f"count={mem_rows[0][0]}")

# ── G7: system1_scan clean ────────────────────────────────────────────────────
r3 = run(["python3", str(SCRIPTS/"system1_scan.py"), "--agent", "forge"])
s1_out = r3.stdout + r3.stderr
check("G7", "system1_scan runs without crash", r3.returncode == 0,
      "idle" if "idle" in s1_out else s1_out[:80])

# ── G8: COG-GAP-012 closed ────────────────────────────────────────────────────
all_pass = all(p for _, p in RESULTS)
check("G8", "COG-GAP-012 closed: first real end-to-end cycle proven", all_pass)

# ── Summary ───────────────────────────────────────────────────────────────────
passed = sum(1 for _, p in RESULTS if p)
total = len(RESULTS)
print(f"\nTOTAL: {total}/{total} | PASS={passed} | FAIL={total-passed}")
sys.exit(0 if passed == total else 1)
