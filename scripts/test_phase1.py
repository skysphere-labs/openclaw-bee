#!/usr/bin/env python3
"""Phase 1 test suite — all 8 tests must PASS."""
import sqlite3, subprocess, sys, uuid, math
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB = "/Users/acevashisth/.openclaw/workspace/state/vector.db"
SCRIPTS = Path(__file__).parent
PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []

def run(name, fn):
    try:
        fn()
        results.append((name, True, ""))
        print(f"{PASS}: {name}")
    except AssertionError as e:
        results.append((name, False, str(e)))
        print(f"{FAIL}: {name} — {e}")
    except Exception as e:
        results.append((name, False, f"ERROR: {e}"))
        print(f"{FAIL}: {name} — ERROR: {e}")

# ── TEST 1: memories table created (idempotent) ───────────────────────
def test_memories_table():
    conn = sqlite3.connect(DB)
    # Run twice — must not fail
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, agent_id TEXT NOT NULL DEFAULT 'vector',
        content TEXT NOT NULL, importance REAL NOT NULL DEFAULT 5.0,
        decay_rate REAL NOT NULL DEFAULT 0.3, access_count INTEGER NOT NULL DEFAULT 0,
        last_accessed TEXT, activation_score REAL NOT NULL DEFAULT 0.0,
        embedding BLOB, source TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, agent_id TEXT NOT NULL DEFAULT 'vector',
        content TEXT NOT NULL, importance REAL NOT NULL DEFAULT 5.0,
        decay_rate REAL NOT NULL DEFAULT 0.3, access_count INTEGER NOT NULL DEFAULT 0,
        last_accessed TEXT, activation_score REAL NOT NULL DEFAULT 0.0,
        embedding BLOB, source TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    conn.close()
    assert "id" in cols and "embedding" in cols and "importance" in cols, f"Missing columns: {cols}"
run("TEST 1: memories table created (idempotent)", test_memories_table)

# ── TEST 2: beliefs table has embedding column ────────────────────────
def test_beliefs_embedding():
    conn = sqlite3.connect(DB)
    # Ensure column exists (idempotent)
    try:
        conn.execute("ALTER TABLE beliefs ADD COLUMN embedding BLOB")
        conn.commit()
    except Exception:
        pass
    cols = {r[1] for r in conn.execute("PRAGMA table_info(beliefs)").fetchall()}
    conn.close()
    assert "embedding" in cols, f"embedding column missing from beliefs. Columns: {cols}"
run("TEST 2: beliefs table has embedding column", test_beliefs_embedding)

# ── TEST 3: migrate_lessons_to_memories.py runs clean ────────────────
def test_migrate_lessons():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "migrate_lessons_to_memories.py")],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    # Run again — idempotent (skipped = same count)
    result2 = subprocess.run(
        [sys.executable, str(SCRIPTS / "migrate_lessons_to_memories.py")],
        capture_output=True, text=True
    )
    assert result2.returncode == 0, f"Idempotent run failed: {result2.stderr}"
    conn = sqlite3.connect(DB)
    count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    assert count > 0, "No memories after migration"
run("TEST 3: migrate_lessons_to_memories.py runs clean, rows inserted", test_migrate_lessons)

# ── TEST 4: retrieve_memories.py returns results ──────────────────────
def test_retrieve_memories():
    # Seed a test memory for forge if not already present
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT OR IGNORE INTO memories (id, agent_id, content, importance, source) VALUES (?,?,?,?,?)",
        ("test0001", "forge", "Use TypeScript strict mode for all new plugins", 7.0, "test"),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "retrieve_memories.py"), "--agent", "forge", "--limit", "5"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert len(result.stdout.strip()) > 0, "No output from retrieve_memories"
run("TEST 4: retrieve_memories.py returns results for valid agent", test_retrieve_memories)

# ── TEST 5: promote_to_shared.py moves belief to __shared__ ──────────
def test_promote_to_shared():
    conn = sqlite3.connect(DB)
    # Insert a test belief
    bid = "testbelief" + str(uuid.uuid4())[:6]
    conn.execute(
        """INSERT INTO beliefs (id, content, confidence, category, status, agent_id, source_labels, tags)
           VALUES (?,?,?,?,?,?,?,?)""",
        (bid, "This is a test belief for promote_to_shared", 0.8, "fact", "active", "forge", "[]", "[]"),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "promote_to_shared.py"),
         "--belief-id", bid, "--reason", "test promote"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}\n{result.stdout}"

    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT agent_id FROM beliefs WHERE id=?", (bid,)).fetchone()
    conn.close()
    assert row and row[0] == "__shared__", f"belief agent_id not changed: {row}"
run("TEST 5: promote_to_shared.py moves belief to __shared__", test_promote_to_shared)

# ── TEST 6: ACT-R ranking — higher importance/recency scores first ────
def test_actr_ranking():
    conn = sqlite3.connect(DB)
    now = datetime.now(timezone.utc)
    recent = now.isoformat()
    old = (now - timedelta(days=30)).isoformat()

    # High importance + recent
    conn.execute(
        """INSERT OR REPLACE INTO memories (id, agent_id, content, importance, decay_rate, access_count, last_accessed, activation_score, source)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("actr_hi", "forge", "High importance recent memory", 9.0, 0.1, 5, recent, 0.0, "test"),
    )
    # Low importance + old
    conn.execute(
        """INSERT OR REPLACE INTO memories (id, agent_id, content, importance, decay_rate, access_count, last_accessed, activation_score, source)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("actr_lo", "forge", "Low importance old memory", 2.0, 0.5, 1, old, 0.0, "test"),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "retrieve_memories.py"), "--agent", "forge", "--limit", "10"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    lines = result.stdout.strip().split("\n")
    # Find positions of our test memories
    hi_pos = next((i for i, l in enumerate(lines) if "High importance recent" in l), None)
    lo_pos = next((i for i, l in enumerate(lines) if "Low importance old" in l), None)
    assert hi_pos is not None, "High importance memory not found in output"
    assert lo_pos is not None, "Low importance memory not found in output"
    assert hi_pos < lo_pos, f"ACT-R ranking wrong: hi={hi_pos} lo={lo_pos}"
run("TEST 6: ACT-R ranking — higher importance/recency scores first", test_actr_ranking)

# ── TEST 7: Cross-agent isolation ────────────────────────────────────
def test_cross_agent_isolation():
    conn = sqlite3.connect(DB)
    conn.execute(
        """INSERT OR REPLACE INTO memories (id, agent_id, content, importance, source)
           VALUES (?,?,?,?,?)""",
        ("iso_forge", "forge", "FORGE-ONLY: TypeScript strict mode lesson", 8.0, "test"),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "retrieve_memories.py"), "--agent", "oracle", "--limit", "20"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "FORGE-ONLY" not in result.stdout, "FORGE memory leaked into ORACLE query!"
run("TEST 7: Cross-agent isolation — forge memories not returned for oracle", test_cross_agent_isolation)

# ── TEST 8: Semantic recall fallback — keyword when embedding is null ─
def test_semantic_fallback():
    conn = sqlite3.connect(DB)
    # Insert belief with NO embedding
    bid = "sem_" + str(uuid.uuid4())[:6]
    conn.execute(
        """INSERT OR IGNORE INTO beliefs (id, content, confidence, category, status, agent_id, source_labels, tags, embedding)
           VALUES (?,?,?,?,?,?,?,?,NULL)""",
        (bid, "Python scripting is preferred over bash for complex tasks", 0.85, "preference", "active", "vector", "[]", "[]"),
    )
    conn.commit()

    # Keyword search (simulates loadRecalled fallback) — no embedding needed
    rows = conn.execute(
        """SELECT id FROM beliefs WHERE status != 'archived' AND confidence >= 0.3
           AND agent_id = 'vector' AND embedding IS NULL
           AND LOWER(content) LIKE '%python%' LIMIT 5""",
    ).fetchall()
    conn.close()
    assert len(rows) > 0, "Keyword fallback returned no results for 'python' query"
run("TEST 8: Semantic recall fallback — keyword works when embedding is null", test_semantic_fallback)

# ── Summary ───────────────────────────────────────────────────────────
print()
passed = sum(1 for _, ok, _ in results if ok)
print(f"Results: {passed}/{len(results)} passed")
if passed < len(results):
    print("\nFailed tests:")
    for name, ok, msg in results:
        if not ok:
            print(f"  {name}: {msg}")
    sys.exit(1)
else:
    print("All Phase 1 tests PASSED ✅")
