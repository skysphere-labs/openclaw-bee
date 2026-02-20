#!/usr/bin/env python3
"""Review/approve/reject pending shared/global beliefs."""

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_DEFAULT = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def cmd_list(conn: sqlite3.Connection):
    rows = conn.execute(
        """SELECT id, source_agent, scope, content, created_at
           FROM pending_shared
           WHERE status='pending'
           ORDER BY created_at ASC"""
    ).fetchall()
    if not rows:
        print("No pending items.")
        return
    for r in rows:
        preview = (r["content"][:100] + "...") if len(r["content"]) > 100 else r["content"]
        print(f"{r['id']} | {r['scope']} | from={r['source_agent']} | at={r['created_at']} | {preview}")


def _insert_belief(conn, *, agent_id: str, content: str, source: str, status: str, scope: str):
    bid = f"rvw-{uuid.uuid4().hex[:10]}"
    conn.execute(
        """INSERT OR IGNORE INTO beliefs
           (id, content, confidence, category, status, agent_id, source, importance, created_at, updated_at, scope)
           VALUES (?,?,0.8,'fact',?,?,?,?,?,?,?)""",
        (bid, content, status, agent_id, source, 7.0, now_iso(), now_iso(), scope),
    )
    return bid


def cmd_approve(conn: sqlite3.Connection, pending_id: str):
    row = conn.execute("SELECT * FROM pending_shared WHERE id=?", (pending_id,)).fetchone()
    if not row:
        print(json.dumps({"ok": False, "error": f"pending id not found: {pending_id}"}))
        return
    if row["status"] != "pending":
        print(json.dumps({"ok": False, "error": f"pending id already {row['status']}"}))
        return

    target_agent = "__shared__" if row["scope"] == "shared" else "chief"
    bid = _insert_belief(
        conn,
        agent_id=target_agent,
        content=row["content"],
        source=f"pending_approved:{row['id']}",
        status="active",
        scope=row["scope"],
    )
    conn.execute(
        """UPDATE pending_shared
           SET status='approved', reviewed_by='vector', reviewed_at=?
           WHERE id=?""",
        (now_iso(), pending_id),
    )
    conn.commit()
    print(json.dumps({"ok": True, "action": "approved", "pending_id": pending_id, "belief_id": bid, "target_agent": target_agent}))


def cmd_reject(conn: sqlite3.Connection, pending_id: str):
    row = conn.execute("SELECT * FROM pending_shared WHERE id=?", (pending_id,)).fetchone()
    if not row:
        print(json.dumps({"ok": False, "error": f"pending id not found: {pending_id}"}))
        return
    if row["status"] != "pending":
        print(json.dumps({"ok": False, "error": f"pending id already {row['status']}"}))
        return

    bid = _insert_belief(
        conn,
        agent_id=row["source_agent"],
        content=row["content"],
        source=f"pending_rejected:{row['id']}",
        status="provisional",
        scope="private",
    )
    conn.execute(
        """UPDATE pending_shared
           SET status='rejected', reviewed_by='vector', reviewed_at=?
           WHERE id=?""",
        (now_iso(), pending_id),
    )
    conn.commit()
    print(json.dumps({"ok": True, "action": "rejected", "pending_id": pending_id, "belief_id": bid, "target_agent": row["source_agent"]}))


def cmd_stats(conn: sqlite3.Connection):
    out = {
        "by_status": dict(conn.execute("SELECT status, COUNT(*) n FROM pending_shared GROUP BY status").fetchall()),
        "by_scope": dict(conn.execute("SELECT scope, COUNT(*) n FROM pending_shared GROUP BY scope").fetchall()),
        "by_source_agent": dict(conn.execute("SELECT source_agent, COUNT(*) n FROM pending_shared GROUP BY source_agent").fetchall()),
    }
    # sqlite rows to tuples conversion handled below
    for k in list(out.keys()):
        if isinstance(out[k], list):
            out[k] = {r[0]: r[1] for r in out[k]}
    print(json.dumps(out))


def main():
    p = argparse.ArgumentParser(description="Review pending shared/global queue")
    p.add_argument("--db", default=str(DB_DEFAULT))
    p.add_argument("--list", action="store_true")
    p.add_argument("--approve", default="")
    p.add_argument("--reject", default="")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    conn = connect(Path(args.db))
    try:
        if args.list:
            cmd_list(conn)
        elif args.approve:
            cmd_approve(conn, args.approve)
        elif args.reject:
            cmd_reject(conn, args.reject)
        elif args.stats:
            cmd_stats(conn)
        else:
            print("Use one of: --list | --approve ID | --reject ID | --stats")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
