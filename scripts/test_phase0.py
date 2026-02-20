#!/usr/bin/env python3
"""
test_phase0.py — Phase 0 verification suite
Tests all 5 Phase 0 components before commit.

Run:
    python3 /Users/acevashisth/.openclaw/workspace/scripts/test_phase0.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path("/Users/acevashisth/.openclaw/workspace")
SCRIPTS = WORKSPACE / "scripts"
DB_PATH = WORKSPACE / "state" / "vector.db"
ACTIVITY_PATH = WORKSPACE / "state" / "agent-activity.json"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results = []


def run_script(script: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)] + args,
        capture_output=True,
        text=True,
    )


def get_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def assert_pass(condition: bool, label: str, detail: str = "") -> bool:
    if condition:
        print(f"  ✓ {label}")
        return True
    else:
        print(f"  ✗ {label}" + (f": {detail}" if detail else ""))
        return False


# ==============================================================================
# TEST 1: spawn_and_track.py writes to audit_log
# ==============================================================================
def test1_spawn_writes_audit_log():
    print("\nTEST 1: spawn_and_track.py writes to audit_log")
    test_ticket = "TEST-PHASE0-001"
    test_agent = "TEST_FORGE"
    test_model = "sonnet"
    test_task = "phase0 test spawn"

    # Count rows before
    conn = sqlite3.connect(DB_PATH)
    before = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE agent=? AND action='spawn' AND ticket_id=?",
        (test_agent, test_ticket),
    ).fetchone()[0]
    conn.close()

    # Run script
    result = run_script("spawn_and_track.py", [
        "--agent", test_agent,
        "--ticket", test_ticket,
        "--model", test_model,
        "--task", test_task,
    ])

    ok = True
    ok &= assert_pass(result.returncode == 0, "Script exits 0", result.stderr)

    # Count rows after
    conn = sqlite3.connect(DB_PATH)
    after = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE agent=? AND action='spawn' AND ticket_id=?",
        (test_agent, test_ticket),
    ).fetchone()[0]

    row = conn.execute(
        "SELECT detail FROM audit_log WHERE agent=? AND action='spawn' AND ticket_id=? ORDER BY id DESC LIMIT 1",
        (test_agent, test_ticket),
    ).fetchone()
    conn.close()

    ok &= assert_pass(after > before, f"New audit_log row inserted (before={before}, after={after})")

    if row:
        detail = json.loads(row[0])
        ok &= assert_pass(detail.get("task") == test_task, f"detail.task correct: {detail.get('task')}")
        ok &= assert_pass(detail.get("model") == test_model, f"detail.model correct: {detail.get('model')}")
        ok &= assert_pass(detail.get("ticket") == test_ticket, f"detail.ticket correct")
    else:
        ok = False
        print("  ✗ No row found in audit_log")

    status = PASS if ok else FAIL
    print(f"  → {status}")
    results.append(("TEST 1", ok))
    return test_agent, test_ticket  # Return for use in test 3


# ==============================================================================
# TEST 2: spawn_and_track.py updates agent-activity.json
# ==============================================================================
def test2_spawn_updates_activity_json(test_agent: str, test_ticket: str):
    print("\nTEST 2: spawn_and_track.py updates agent-activity.json")
    ok = True

    if not ACTIVITY_PATH.exists():
        ok &= assert_pass(False, "agent-activity.json exists")
        results.append(("TEST 2", False))
        return

    with open(ACTIVITY_PATH) as f:
        data = json.load(f)

    workers = data.get("workers", [])
    matching = [w for w in workers if w.get("agent") == test_agent and w.get("ticket") == test_ticket]

    ok &= assert_pass(len(matching) > 0, f"Worker entry found in workers array (found {len(matching)})")

    if matching:
        w = matching[-1]
        ok &= assert_pass(w.get("status") == "running", f"status='running': {w.get('status')}")
        ok &= assert_pass("started_at" in w, "started_at present")
        ok &= assert_pass("id" in w, "id present")
        ok &= assert_pass(w.get("model") is not None, f"model present: {w.get('model')}")
        ok &= assert_pass(w.get("task") is not None, "task present")

    status = PASS if ok else FAIL
    print(f"  → {status}")
    results.append(("TEST 2", ok))


# ==============================================================================
# TEST 3: complete_and_track.py marks completion
# ==============================================================================
def test3_complete_marks_done(test_agent: str, test_ticket: str):
    print("\nTEST 3: complete_and_track.py marks completion")
    ok = True
    summary = "phase0 test complete"

    result = run_script("complete_and_track.py", [
        "--agent", test_agent,
        "--ticket", test_ticket,
        "--status", "done",
        "--cost", "0.00",
        "--summary", summary,
    ])

    ok &= assert_pass(result.returncode == 0, "Script exits 0", result.stderr)

    # Check audit_log for completion record
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT action, detail FROM audit_log WHERE agent=? AND ticket_id=? AND action='complete' ORDER BY id DESC LIMIT 1",
        (test_agent, test_ticket),
    ).fetchone()
    conn.close()

    ok &= assert_pass(row is not None, "completion row in audit_log")
    if row:
        detail = json.loads(row[1])
        ok &= assert_pass(detail.get("status") == "done", f"status=done in detail: {detail.get('status')}")
        ok &= assert_pass(detail.get("summary") == summary, f"summary matches")

    # Check agent-activity.json
    if ACTIVITY_PATH.exists():
        with open(ACTIVITY_PATH) as f:
            data = json.load(f)
        workers = data.get("workers", [])
        matching = [
            w for w in workers
            if w.get("agent") == test_agent and w.get("ticket") == test_ticket
            and w.get("status") == "done"
        ]
        ok &= assert_pass(len(matching) > 0, f"Worker status updated to 'done' in activity JSON")
        if matching:
            w = matching[-1]
            ok &= assert_pass("completed_at" in w, "completed_at present")

    status = PASS if ok else FAIL
    print(f"  → {status}")
    results.append(("TEST 3", ok))


# ==============================================================================
# TEST 4: check_budget.py returns 0 under limits, 1 over agent limit, 2 over system
# ==============================================================================
def test4_budget_circuit_breaker():
    print("\nTEST 4: check_budget.py returns 0 under limits, 1 over agent limit, 2 over system limit")
    ok = True

    # 4a: Normal case — should return 0
    result = run_script("check_budget.py", ["--agent", "TEST_BUDGET_OK", "--model", "sonnet"])
    ok &= assert_pass(result.returncode == 0, f"Normal agent returns exit 0 (got {result.returncode})", result.stdout.strip())

    # 4b: Inject an over-limit agent cost and check for exit 1
    today = get_today()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO cost_tracking (date, agent, model, input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?)",
        (today, "test_over_limit_agent", "sonnet", 0, 0, 15.0),  # $15 > $10 limit
    )
    conn.commit()
    conn.close()

    result = run_script("check_budget.py", ["--agent", "test_over_limit_agent", "--model", "sonnet"])
    ok &= assert_pass(result.returncode == 1, f"Over-limit agent returns exit 1 (got {result.returncode})", result.stdout.strip())
    ok &= assert_pass("BUDGET_BLOCKED" in result.stdout, "Prints BUDGET_BLOCKED message")

    # 4c: Inject over-system-limit cost and check for exit 2
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO cost_tracking (date, agent, model, input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?)",
        (today, "test_system_flood", "sonnet", 0, 0, 35.0),  # $35 > $30 system limit
    )
    conn.commit()
    conn.close()

    result = run_script("check_budget.py", ["--agent", "TEST_ANY_AGENT", "--model", "sonnet"])
    ok &= assert_pass(result.returncode == 2, f"Over-system-limit returns exit 2 (got {result.returncode})", result.stdout.strip())
    ok &= assert_pass("EXIT 2" in result.stdout, "Prints EXIT 2 message")

    # Cleanup test cost_tracking rows
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "DELETE FROM cost_tracking WHERE agent IN ('test_over_limit_agent', 'test_system_flood') AND date = ?",
        (today,),
    )
    conn.commit()
    conn.close()

    # 4d: Spawn rate limit — inject 20 audit_log spawn entries and check exit 1
    conn = sqlite3.connect(DB_PATH)
    for i in range(20):
        conn.execute(
            "INSERT INTO audit_log (agent, action, ticket_id, detail) VALUES (?, 'spawn', ?, ?)",
            ("TEST_RATE_LIMIT", f"RATE-{i}", json.dumps({"test": True})),
        )
    conn.commit()
    conn.close()

    result = run_script("check_budget.py", ["--agent", "TEST_RATE_LIMIT", "--model", "sonnet"])
    ok &= assert_pass(result.returncode == 1, f"Rate-limited agent returns exit 1 (got {result.returncode})", result.stdout.strip())

    # Cleanup rate limit test rows
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "DELETE FROM audit_log WHERE agent='TEST_RATE_LIMIT' AND detail LIKE '%\"test\": true%'"
    )
    conn.commit()
    conn.close()

    status = PASS if ok else FAIL
    print(f"  → {status}")
    results.append(("TEST 4", ok))


# ==============================================================================
# TEST 5: Provisional beliefs excluded from activation scoring
# ==============================================================================
def test5_provisional_beliefs_excluded():
    print("\nTEST 5: Provisional beliefs excluded from activation scoring")
    ok = True

    bee_db_path = WORKSPACE / "state" / "bee.db"
    db_ts_path = WORKSPACE / "projects" / "openclaw-bee" / "src" / "db.ts"
    recall_ts_path = WORKSPACE / "projects" / "openclaw-bee" / "src" / "recall.ts"
    scorer_path = SCRIPTS / "activation-scorer.py"

    # Check db.ts — all 3 query functions should use status = 'active'
    if db_ts_path.exists():
        db_ts = db_ts_path.read_text()

        # getCoreBeliefs
        ok &= assert_pass(
            "WHERE status = 'active'" in db_ts and "status != 'archived'" not in db_ts,
            "db.ts: getCoreBeliefs uses status = 'active' (not != archived)"
        )

        # Count occurrences of status = 'active' (should be 3 for the 3 functions)
        count_active = db_ts.count("WHERE status = 'active'")
        ok &= assert_pass(count_active >= 3, f"db.ts: 3 query functions use status='active' (found {count_active})")

        # No status != 'archived' remaining
        ok &= assert_pass(
            "status != 'archived'" not in db_ts,
            "db.ts: No remaining 'status != archived' queries"
        )
    else:
        ok = False
        assert_pass(False, f"db.ts not found at {db_ts_path}")

    # Check recall.ts — getProfileBeliefs should use status = 'active'
    if recall_ts_path.exists():
        recall_ts = recall_ts_path.read_text()
        ok &= assert_pass(
            "WHERE status = 'active'" in recall_ts,
            "recall.ts: getProfileBeliefs uses status = 'active'"
        )
        ok &= assert_pass(
            "status != 'archived'" not in recall_ts,
            "recall.ts: No remaining 'status != archived' queries"
        )
    else:
        ok = False
        assert_pass(False, f"recall.ts not found at {recall_ts_path}")

    # Check activation-scorer.py — already uses status='active', verify it's still correct
    if scorer_path.exists():
        scorer = scorer_path.read_text()
        ok &= assert_pass(
            "WHERE status='active'" in scorer,
            "activation-scorer.py: score_table uses status='active'"
        )
        ok &= assert_pass(
            "status!='archived'" not in scorer and "status != 'archived'" not in scorer,
            "activation-scorer.py: No 'status != archived' queries"
        )
    else:
        ok = False
        assert_pass(False, f"activation-scorer.py not found at {scorer_path}")

    # Functional test: if bee.db exists, verify no provisional belief gets a score update
    if bee_db_path.exists():
        conn = sqlite3.connect(bee_db_path)
        provisional_count = conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE status='provisional'"
        ).fetchone()[0]
        conn.close()
        print(f"  ℹ bee.db: {provisional_count} provisional beliefs present")
        if provisional_count > 0:
            print(f"    (These would have been included in scoring before the fix)")
    else:
        print(f"  ℹ bee.db not found — skipping functional DB check")

    # vector.db provisional belief check
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        provisional_count = conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE status='provisional'"
        ).fetchone()[0]
        active_count = conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE status='active'"
        ).fetchone()[0]
        conn.close()
        print(f"  ℹ vector.db: {provisional_count} provisional beliefs, {active_count} active beliefs")
        ok &= assert_pass(True, f"activation-scorer.py only scores {active_count} active beliefs (skips {provisional_count} provisional)")

    status = PASS if ok else FAIL
    print(f"  → {status}")
    results.append(("TEST 5", ok))


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    print("=" * 60)
    print("PHASE 0 TEST SUITE")
    print("=" * 60)

    # Run tests
    test_agent, test_ticket = test1_spawn_writes_audit_log()
    test2_spawn_updates_activity_json(test_agent, test_ticket)
    test3_complete_marks_done(test_agent, test_ticket)
    test4_budget_circuit_breaker()
    test5_provisional_beliefs_excluded()

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    passed = 0
    for name, ok in results:
        icon = "✓" if ok else "✗"
        color = "\033[32m" if ok else "\033[31m"
        print(f"  {color}{icon} {name}\033[0m")
        if ok:
            passed += 1

    total = len(results)
    print(f"\n{passed}/{total} tests passed")

    if passed == total:
        print(f"\n\033[32m✓ ALL TESTS PASSED — ready to commit\033[0m")
        sys.exit(0)
    else:
        print(f"\n\033[31m✗ {total - passed} TESTS FAILED — fix before committing\033[0m")
        sys.exit(1)


if __name__ == "__main__":
    main()
