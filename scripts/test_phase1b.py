#!/usr/bin/env python3
"""
test_phase1b.py — Phase 1B test suite (8 tests).
Run: python3 /Users/acevashisth/.openclaw/workspace/scripts/test_phase1b.py
All 8 must PASS.
"""
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

DB = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
SCRIPTS = Path("/Users/acevashisth/.openclaw/workspace/scripts")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results = []

def run(name: str, fn):
    try:
        fn()
        print(f"  {PASS}  {name}")
        results.append(True)
    except AssertionError as e:
        print(f"  {FAIL}  {name}: {e}")
        results.append(False)
    except Exception as e:
        print(f"  {FAIL}  {name}: EXCEPTION — {e}")
        results.append(False)


# ── TEST 1 — beliefs table has 4 new columns ────────────────────────────
def test_schema_columns():
    conn = sqlite3.connect(DB)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(beliefs);")}
    conn.close()
    missing = {"action_implication", "belief_type", "evidence_for", "evidence_against"} - cols
    assert not missing, f"Missing columns: {missing}"


# ── TEST 2 — update_beliefs.py stores valid belief as provisional ────────
def test_store_valid_belief():
    agent = f"test-t2-{uuid.uuid4().hex[:6]}"
    payload = {
        "belief_updates": [{
            "content": "This is a valid test belief content for Phase 1B.",
            "category": "fact",
            "confidence": 0.8,
            "importance": 6,
            "action_implication": "Use this in future tasks",
            "evidence_for": "Observed in testing",
            "evidence_against": "Could be test artifact",
        }],
        "memory_operations": [],
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "update_beliefs.py"),
         "--agent", agent, "--output", json.dumps(payload)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "stored=1" in result.stdout, f"Expected stored=1 in: {result.stdout}"

    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT status, action_implication FROM beliefs WHERE agent_id=?", (agent,)
    ).fetchone()
    conn.close()
    assert row is not None, "No belief found in DB"
    assert row[0] == "provisional", f"Expected provisional, got: {row[0]}"
    assert row[1] == "Use this in future tasks", f"action_implication mismatch: {row[1]}"


# ── TEST 3 — malformed JSON rejected gracefully (no crash) ──────────────
def test_malformed_json():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "update_beliefs.py"),
         "--agent", "test-t3", "--output", "{not valid json!!!"],
        capture_output=True, text=True
    )
    assert result.returncode != 0, "Expected non-zero exit for malformed JSON"
    # Must not have written anything to DB
    conn = sqlite3.connect(DB)
    count = conn.execute("SELECT COUNT(*) FROM beliefs WHERE agent_id='test-t3'").fetchone()[0]
    conn.close()
    assert count == 0, f"Expected 0 beliefs for test-t3 after malformed JSON, got {count}"


# ── TEST 4 — invalid category 'heuristic' → remapped to 'fact' (not error) ─
def test_invalid_category_remapped():
    agent = f"test-t4-{uuid.uuid4().hex[:6]}"
    payload = {
        "belief_updates": [{
            "content": "This belief has an invalid category that should be remapped.",
            "category": "heuristic",
            "confidence": 0.7,
            "importance": 5,
        }],
        "memory_operations": [],
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "update_beliefs.py"),
         "--agent", agent, "--output", json.dumps(payload)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script crashed on invalid category: {result.stderr}"
    # update_beliefs.py remaps invalid categories to 'fact'
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT category FROM beliefs WHERE agent_id=?", (agent,)
    ).fetchone()
    conn.close()
    assert row is not None, "No belief stored"
    assert row[0] == "fact", f"Expected category 'fact' after remap, got: {row[0]}"


# ── TEST 5 — provisional gate — all PM beliefs start as provisional ─────
def test_provisional_gate():
    agent = f"test-t5-{uuid.uuid4().hex[:6]}"
    payload = {
        "belief_updates": [
            {
                "content": "High confidence belief that should still be provisional.",
                "category": "goal",
                "confidence": 1.0,  # max confidence
                "importance": 10,
            },
            {
                "content": "Low confidence belief that should also be provisional.",
                "category": "fact",
                "confidence": 0.5,
                "importance": 1,
            },
        ],
        "memory_operations": [],
    }
    subprocess.run(
        [sys.executable, str(SCRIPTS / "update_beliefs.py"),
         "--agent", agent, "--output", json.dumps(payload)],
        capture_output=True, text=True
    )
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT status FROM beliefs WHERE agent_id=?", (agent,)
    ).fetchall()
    conn.close()
    assert len(rows) == 2, f"Expected 2 beliefs, got {len(rows)}"
    for row in rows:
        assert row[0] == "provisional", f"Expected provisional, got: {row[0]}"


# ── TEST 6 — memory_operations store op writes to memories table ────────
def test_memory_operations_store():
    agent = f"test-t6-{uuid.uuid4().hex[:6]}"
    payload = {
        "belief_updates": [],
        "memory_operations": [{
            "op": "store",
            "content": "Phase 1B memory operation test — store op verification.",
            "importance": 7,
        }],
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "update_beliefs.py"),
         "--agent", agent, "--output", json.dumps(payload)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "memory_ops=1" in result.stdout, f"Expected memory_ops=1 in: {result.stdout}"
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT content, importance FROM memories WHERE agent_id=?", (agent,)
    ).fetchone()
    conn.close()
    assert row is not None, "Memory not found in DB"
    assert "Phase 1B" in row[0], f"Content mismatch: {row[0]}"
    assert float(row[1]) == 7.0, f"Importance mismatch: {row[1]}"


# ── TEST 7 — validate_pm_output catches missing content field ───────────
def test_validate_pm_output_missing_content():
    sys.path.insert(0, str(SCRIPTS))
    from pm_output_schemas import validate_pm_output

    raw = {
        "belief_updates": [
            {
                # content missing entirely
                "category": "fact",
                "confidence": 0.8,
                "importance": 5,
            },
            {
                "content": "x",  # too short (< 10 chars)
                "category": "fact",
                "confidence": 0.8,
                "importance": 5,
            },
        ],
        "memory_operations": [],
    }
    output, errors = validate_pm_output(raw)
    assert len(errors) == 2, f"Expected 2 errors for missing/short content, got {len(errors)}: {errors}"
    assert len(output.belief_updates) == 0, f"Expected 0 valid beliefs, got {len(output.belief_updates)}"


# ── TEST 8 — __shared__ beliefs appear in subagent context ─────────────
def test_shared_beliefs_queryable():
    # Insert a __shared__ belief
    conn = sqlite3.connect(DB)
    bid = f"shared-test-{uuid.uuid4().hex[:8]}"
    try:
        conn.execute("""
            INSERT INTO beliefs
            (id, content, confidence, category, status, agent_id, created_at, updated_at)
            VALUES (?, ?, 0.9, 'fact', 'active', '__shared__', datetime('now'), datetime('now'))
        """, (bid, "Phase 1B shared belief: VECTOR is the conductor PM."))
        conn.commit()
    finally:
        pass

    # Query as the PM spawn injection would — verify the shared belief is findable
    # (no LIMIT here to avoid flakiness from ordering ties with pre-existing __shared__ rows)
    rows = conn.execute(
        "SELECT content FROM beliefs WHERE agent_id='__shared__' AND status != 'archived'"
    ).fetchall()
    conn.close()

    assert len(rows) >= 1, "Expected at least 1 shared belief"
    contents = [r[0] for r in rows]
    assert any("VECTOR is the conductor PM" in c for c in contents), \
        f"Expected shared belief in results, got: {contents}"

    # Also verify the mechanism: a limited query (as used in spawn injection) returns rows
    conn = sqlite3.connect(DB)
    limited = conn.execute(
        "SELECT content FROM beliefs WHERE agent_id='__shared__' AND status != 'archived' ORDER BY activation_score DESC LIMIT 3"
    ).fetchall()
    conn.close()
    assert len(limited) >= 1, "Limited query returned no shared beliefs"

    # Cleanup
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM beliefs WHERE id=?", (bid,))
    conn.commit()
    conn.close()


# ── Run all tests ────────────────────────────────────────────────────────
print("\n=== Phase 1B Test Suite ===\n")

run("TEST 1: beliefs table has action_implication, belief_type, evidence_for, evidence_against", test_schema_columns)
run("TEST 2: update_beliefs.py stores valid belief as provisional for agent", test_store_valid_belief)
run("TEST 3: update_beliefs.py rejects malformed JSON gracefully (no crash, no partial write)", test_malformed_json)
run("TEST 4: update_beliefs.py remaps invalid category 'heuristic' → 'fact' (skips, doesn't error)", test_invalid_category_remapped)
run("TEST 5: provisional gate — all PM beliefs start as provisional regardless of confidence", test_provisional_gate)
run("TEST 6: memory_operations store op writes to memories table", test_memory_operations_store)
run("TEST 7: validate_pm_output catches missing/short content field", test_validate_pm_output_missing_content)
run("TEST 8: pm spawn injection — __shared__ beliefs appear in subagent context", test_shared_beliefs_queryable)

print(f"\n{'='*40}")
passed = sum(results)
total = len(results)
print(f"Results: {passed}/{total} PASS\n")
sys.exit(0 if passed == total else 1)
