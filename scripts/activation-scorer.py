#!/usr/bin/env python3
import math
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")


def parse_dt(value):
    if not value:
        return None
    s = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def safe_hours_since(now, dt):
    if not dt:
        return 1.0
    h = (now - dt).total_seconds() / 3600.0
    return max(h, 1.0 / 3600.0)


def access_times(now, created_at, last_accessed, access_count):
    created = parse_dt(created_at) or now
    last = parse_dt(last_accessed) or created
    n = int(access_count or 0)

    if n <= 0:
        return [created]
    if n == 1:
        return [last]

    span = (last - created).total_seconds()
    if span <= 0:
        return [last for _ in range(n)]

    step = span / (n - 1)
    return [created + (last - created) * (i / (n - 1)) for i in range(n)]


def compute_activation(now, created_at, last_accessed, access_count, decay_rate, importance, confidence):
    d = float(decay_rate if decay_rate is not None else 0.5)
    d = min(max(d, 0.01), 2.0)

    times = access_times(now, created_at, last_accessed, access_count)
    total = 0.0
    for t in times:
        h = safe_hours_since(now, t)
        total += h ** (-d)

    base_level = math.log(max(total, 1e-12))
    importance_bonus = float(importance if importance is not None else 0.5) * 2.0
    confidence_weight = (float(confidence if confidence is not None else 0.5) - 0.5) * 0.5
    noise = random.gauss(0, 0.1)
    return base_level + importance_bonus + confidence_weight + noise


def score_table(conn, table, id_col):
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()

    cur.execute(
        f"""
        SELECT {id_col}, content, created_at, last_accessed, access_count, decay_rate, importance, confidence
        FROM {table}
        WHERE status='active'
        """
    )
    rows = cur.fetchall()

    scored = []
    for row in rows:
        rid, content, created_at, last_accessed, access_count, decay_rate, importance, confidence = row
        act = compute_activation(now, created_at, last_accessed, access_count, decay_rate, importance, confidence)
        cur.execute(
            f"UPDATE {table} SET activation_score=?, last_activation_calc=? WHERE {id_col}=?",
            (act, now_str, rid),
        )
        scored.append((rid, content or "", act))

    conn.commit()
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored


def print_ranked(title, rows, n=10, reverse=False):
    print(f"\n{title}")
    if not rows:
        print("(none)")
        return
    pick = rows[-n:] if reverse else rows[:n]
    if reverse:
        pick = list(reversed(pick))
    for i, (_, content, score) in enumerate(pick, 1):
        snippet = content.replace("\n", " ")[:100]
        print(f"{i:2d}. {score:8.4f} | {snippet}")


def main():
    conn = sqlite3.connect(DB_PATH)
    mem = score_table(conn, "memory_entries", "id")
    bel = score_table(conn, "beliefs", "id")

    print(f"memory_entries scored: {len(mem)}")
    print(f"beliefs scored: {len(bel)}")

    print_ranked("Top 10 memory_entries by activation", mem, n=10)
    print_ranked("Bottom 10 memory_entries by activation", mem, n=10, reverse=True)
    print_ranked("Top 10 beliefs by activation", bel, n=10)
    print_ranked("Bottom 10 beliefs by activation", bel, n=10, reverse=True)

    conn.close()


if __name__ == "__main__":
    main()
