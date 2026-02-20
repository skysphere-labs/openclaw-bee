#!/usr/bin/env python3
"""Adversarial tests for COG-012 Phase 5B chief namespace in PM spawn context."""

import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path('/Users/acevashisth/.openclaw/workspace/scripts')
DB_PATH = Path('/Users/acevashisth/.openclaw/workspace/state/vector.db')
sys.path.insert(0, str(SCRIPTS))

from build_pm_cognition_block import build_pm_cognition_block
from post_pm import process_output

PASS = 0
FAIL = 0


def rec(test, ok, detail):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"✅ [{test}] PASS - {detail}")
    else:
        FAIL += 1
        print(f"❌ [{test}] FAIL - {detail}")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def make_test_db() -> Path:
    tmp = Path(tempfile.gettempdir()) / f"vector_phase5b_{uuid.uuid4().hex[:10]}.db"
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(tmp))
    src.backup(dst)
    src.close()
    dst.close()
    return tmp


def cleanup_db(dbp: Path):
    try:
        if dbp.exists():
            dbp.unlink()
    except Exception:
        pass


def seed_chief(conn, content, importance, status='active'):
    bid = f"p5b-{uuid.uuid4().hex[:10]}"
    conn.execute(
        """INSERT INTO beliefs
           (id, content, confidence, category, status, agent_id, source, importance,
            activation_score, created_at, updated_at, scope)
           VALUES (?,?,?,?,?,'chief','test_phase5b',?,?,?,?,'private')""",
        (bid, content, 0.9, 'preference', status, float(importance), float(importance), now_iso(), now_iso()),
    )


def test_1_empty_chief():
    dbp = make_test_db()
    conn = sqlite3.connect(str(dbp))
    conn.execute("DELETE FROM beliefs WHERE agent_id='chief'")
    conn.commit(); conn.close()
    block = build_pm_cognition_block(dbp, 'forge')
    ok = ("Chief's observed preferences" not in block) and ("<pm-cognition>" in block)
    rec('T1', ok, 'empty chief namespace handled without crash')
    cleanup_db(dbp)


def test_2_ordering_desc_importance():
    dbp = make_test_db()
    conn = sqlite3.connect(str(dbp))
    conn.execute("DELETE FROM beliefs WHERE agent_id='chief'")
    seed_chief(conn, 'Low importance chief belief', 2)
    seed_chief(conn, 'Top importance chief belief', 9)
    seed_chief(conn, 'Mid importance chief belief', 5)
    conn.commit(); conn.close()
    block = build_pm_cognition_block(dbp, 'forge')
    ok = block.find('Top importance chief belief') < block.find('Mid importance chief belief') < block.find('Low importance chief belief')
    rec('T2', ok, 'chief beliefs sorted by importance DESC')
    cleanup_db(dbp)


def test_3_top5_cap():
    dbp = make_test_db()
    conn = sqlite3.connect(str(dbp))
    conn.execute("DELETE FROM beliefs WHERE agent_id='chief'")
    for i in range(8):
        seed_chief(conn, f'Chief belief imp-{i+1}', i + 1)
    conn.commit(); conn.close()
    block = build_pm_cognition_block(dbp, 'forge')
    lines = [ln for ln in block.splitlines() if 'Chief belief imp-' in ln]
    ok = len(lines) == 5 and all(f'imp-{n}' not in block for n in [1, 2, 3])
    rec('T3', ok, f'chief top-5 cap enforced, count={len(lines)}')
    cleanup_db(dbp)


def test_4_post_pm_blocks_chief():
    dbp = make_test_db()
    out = {'belief_updates': [{'content': 'chief protected namespace write attempt should be blocked', 'confidence': 0.9, 'importance': 7, 'category': 'fact'}]}
    res = process_output('chief', out, dbp)
    ok = res['stored'] == 0 and any('BLOCKED' in e for e in res['errors'])
    rec('T4', ok, str(res))
    cleanup_db(dbp)


def test_5_post_pm_blocks_shared_still():
    dbp = make_test_db()
    out = {'belief_updates': [{'content': '__shared__ protected namespace write attempt should be blocked', 'confidence': 0.9, 'importance': 7, 'category': 'fact'}]}
    res = process_output('__shared__', out, dbp)
    ok = res['stored'] == 0 and any('BLOCKED' in e for e in res['errors'])
    rec('T5', ok, str(res))
    cleanup_db(dbp)


def test_6_read_only_label_verbatim():
    dbp = make_test_db()
    conn = sqlite3.connect(str(dbp))
    conn.execute("DELETE FROM beliefs WHERE agent_id='chief'")
    seed_chief(conn, 'Chief read-only label check belief', 8)
    conn.commit(); conn.close()
    block = build_pm_cognition_block(dbp, 'forge')
    label = "## Chief's observed preferences (read-only — extracted from real interactions)"
    ok = label in block
    rec('T6', ok, 'read-only label appears verbatim')
    cleanup_db(dbp)


def test_7_provisional_filtered_out():
    dbp = make_test_db()
    conn = sqlite3.connect(str(dbp))
    conn.execute("DELETE FROM beliefs WHERE agent_id='chief'")
    seed_chief(conn, 'Chief ACTIVE belief visible', 9, status='active')
    seed_chief(conn, 'Chief PROVISIONAL belief hidden', 10, status='provisional')
    conn.commit(); conn.close()
    block = build_pm_cognition_block(dbp, 'forge')
    ok = ('Chief ACTIVE belief visible' in block) and ('Chief PROVISIONAL belief hidden' not in block)
    rec('T7', ok, 'status gate enforced: only active chief beliefs included')
    cleanup_db(dbp)


def test_8_spawn_integration_live():
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / 'spawn_pm.py'), '--agent', 'forge', '--task', 'integration check for chief section', '--ticket', 'COG-012-T8', '--db', str(DB_PATH)],
        capture_output=True, text=True
    )
    output = (r.stdout or '') + (r.stderr or '')
    ok = r.returncode == 0 and "## Chief's observed preferences" in output
    rec('T8', ok, output[:220].replace('\n', ' | '))


def main():
    test_1_empty_chief()
    test_2_ordering_desc_importance()
    test_3_top5_cap()
    test_4_post_pm_blocks_chief()
    test_5_post_pm_blocks_shared_still()
    test_6_read_only_label_verbatim()
    test_7_provisional_filtered_out()
    test_8_spawn_integration_live()
    total = PASS + FAIL
    print(f"\nTOTAL: {total}/8 | PASS={PASS} | FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
