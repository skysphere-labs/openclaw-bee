#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path('/Users/acevashisth/.openclaw/workspace/state/vector.db')

HIGH = [
    'never', 'always', 'critical', 'security', 'chief directive', 'prime directive',
    'identity', 'architecture rule', 'directive'
]
MID_HIGH = [
    'decision', 'blocker', 'deployed', 'done', 'architecture', 'api key', 'credential', 'token'
]
MID = ['todo', 'meeting', 'strategy', 'project context', 'roadmap']
LOW_MID = ['daily', 'routine', 'status update', 'standup', 'progress update']
LOW = ['temporary', 'debug', 'superseded', 'scratch', 'wip']


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def infer_importance(content: str) -> float:
    text = (content or '').lower()
    if any(k in text for k in HIGH):
        return 0.95
    if any(k in text for k in MID_HIGH):
        return 0.75
    if any(k in text for k in MID):
        return 0.55
    if any(k in text for k in LOW_MID):
        return 0.35
    if any(k in text for k in LOW):
        return 0.15
    return 0.45


def infer_decay(entry_type: str, content: str = '') -> float:
    t = (entry_type or '').lower().strip()
    if t in ('rule', 'directive', 'identity'):
        return 0.1
    if t in ('decision', 'architecture'):
        return 0.2
    if t == 'project':
        return 0.3
    if t in ('note', 'observation', 'memory', ''):
        return 0.5
    if t in ('daily', 'status'):
        return 0.7

    # Fallback inference from content for tables without entry_type (e.g., beliefs)
    text = (content or '').lower()
    if any(k in text for k in ['directive', 'rule', 'identity']):
        return 0.1
    if any(k in text for k in ['decision', 'architecture']):
        return 0.2
    if 'project' in text:
        return 0.3
    if any(k in text for k in ['daily', 'status']):
        return 0.7
    return 0.5


def seed_memory_entries(cur):
    cur.execute("SELECT id, content, entry_type FROM memory_entries WHERE status='active'")
    rows = cur.fetchall()
    for rid, content, entry_type in rows:
        importance = clamp(infer_importance(content))
        decay = infer_decay(entry_type, content)
        cur.execute(
            "UPDATE memory_entries SET importance=?, decay_rate=? WHERE id=?",
            (importance, decay, rid),
        )
    return len(rows)


def seed_beliefs(cur):
    cur.execute("SELECT id, content FROM beliefs WHERE status='active'")
    rows = cur.fetchall()
    for rid, content in rows:
        importance = clamp(infer_importance(content))
        decay = infer_decay('', content)
        cur.execute(
            "UPDATE beliefs SET importance=?, decay_rate=? WHERE id=?",
            (importance, decay, rid),
        )
    return len(rows)


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    m = seed_memory_entries(cur)
    b = seed_beliefs(cur)

    conn.commit()
    conn.close()

    print(f'importance seeded for memory_entries: {m}')
    print(f'importance seeded for beliefs: {b}')


if __name__ == '__main__':
    main()
