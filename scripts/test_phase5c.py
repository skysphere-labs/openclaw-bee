#!/usr/bin/env python3
"""Adversarial tests for COG-013 Phase 5C query expansion retrieval."""

import json
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

import expand_retrieval_query as erq
import spawn_pm

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
    tmp = Path(tempfile.gettempdir()) / f"vector_phase5c_{uuid.uuid4().hex[:10]}.db"
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


def test_2_attack_api_failure_fallback_nonempty():
    bad_task = "\x00\x01\x02\uffff\udbff\udfff impossible ###"
    kws = erq.expand_retrieval_query(bad_task, 'forge')
    ok = isinstance(kws, list) and len(kws) >= 1
    rec('T2', ok, f'fallback keywords={kws}')


def test_3_attack_empty_task_no_crash():
    kws = erq.expand_retrieval_query('', 'forge')
    ok = isinstance(kws, list)
    rec('T3', ok, f'empty task handled, keywords={kws}')


def test_5_attack_zero_matches_no_crash():
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / 'retrieve_memories.py'), '--agent', 'forge', '--queries', 'zzzz_nomatch_12345', '--limit', '5'],
        capture_output=True, text=True
    )
    out = (r.stdout or '').strip()
    ok = r.returncode == 0 and out == ''
    rec('T5', ok, f'returncode={r.returncode}, output_len={len(out)}')


def test_1_expansion_quality_live_api():
    kws = erq.expand_retrieval_query('fix JWT authentication bug', 'forge')
    joined = ' '.join(kws).lower()
    indicators = ['jwt', 'auth', 'token', 'bearer', 'rs256', '401']
    ok = isinstance(kws, list) and len(kws) >= 1 and any(x in joined for x in indicators)
    rec('T1', ok, f'keywords={kws}')


def test_4_multi_keyword_retrieval_or_match():
    conn = sqlite3.connect(str(DB_PATH))
    aid = f'p5c-a-{uuid.uuid4().hex[:8]}'
    bid = f'p5c-b-{uuid.uuid4().hex[:8]}'
    agent = f'test-p5c-{uuid.uuid4().hex[:6]}'
    conn.execute("INSERT INTO memories (id, agent_id, content, importance, decay_rate, access_count, last_accessed, activation_score, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (aid, agent, 'JWT token auth RS256 signing', 9.0, 0.1, 3, now_iso(), 0.0, 'test_phase5c', now_iso()))
    conn.execute("INSERT INTO memories (id, agent_id, content, importance, decay_rate, access_count, last_accessed, activation_score, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (bid, agent, 'completely unrelated content about databases', 9.0, 0.1, 3, now_iso(), 0.0, 'test_phase5c', now_iso()))
    conn.commit(); conn.close()

    r = subprocess.run(
        [sys.executable, str(SCRIPTS / 'retrieve_memories.py'), '--agent', agent, '--queries', 'jwt', 'rs256', 'auth', '--limit', '10'],
        capture_output=True, text=True
    )
    out = (r.stdout or '').lower()
    ok = ('jwt token auth rs256 signing' in out) and ('unrelated content about databases' not in out)
    rec('T4', ok, out.strip()[:180])

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM memories WHERE id IN (?,?)", (aid, bid))
    conn.commit(); conn.close()


def test_6_backward_compat_query_unchanged():
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / 'retrieve_memories.py'), '--agent', 'forge', '--query', 'authentication jwt', '--limit', '3'],
        capture_output=True, text=True
    )
    ok = r.returncode == 0
    rec('T6', ok, f'--query path still works, stdout_len={len((r.stdout or "").strip())}')


def test_7_spawn_pm_end_to_end_contains_memories_section():
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / 'spawn_pm.py'), '--agent', 'forge', '--task', 'implement JWT RS256 authentication for API', '--ticket', 'COG-013-T7'],
        capture_output=True, text=True
    )
    out = (r.stdout or '') + (r.stderr or '')
    ok = r.returncode == 0 and '<memories>' in out and '</memories>' in out and 'query expansion' not in out.lower()
    rec('T7', ok, out[:220].replace('\n', ' | '))


def test_8_attack_expand_returns_empty_spawn_fallback_proceeds():
    original = spawn_pm.expand_keywords
    try:
        spawn_pm.expand_keywords = lambda agent, task: []
        ctx = spawn_pm.build_cognitive_context('forge', 'fix jwt auth bearer token', 'COG-013-T8', DB_PATH)
        ok = '<cognitive-context' in ctx and '<memories>' in ctx
        rec('T8', ok, 'spawn_pm proceeds when expansion is empty list')
    finally:
        spawn_pm.expand_keywords = original


def main():
    # attack first
    test_2_attack_api_failure_fallback_nonempty()
    test_3_attack_empty_task_no_crash()
    test_5_attack_zero_matches_no_crash()

    # normal + integration
    test_1_expansion_quality_live_api()
    test_4_multi_keyword_retrieval_or_match()
    test_6_backward_compat_query_unchanged()
    test_7_spawn_pm_end_to_end_contains_memories_section()
    test_8_attack_expand_returns_empty_spawn_fallback_proceeds()

    total = PASS + FAIL
    print(f"\nTOTAL: {total}/8 | PASS={PASS} | FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
