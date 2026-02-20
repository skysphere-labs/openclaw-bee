#!/usr/bin/env python3
"""
spawn_and_track.py — Phase 0 spawn tracking
Records every PM/worker spawn into audit_log (SQLite) and agent-activity.json atomically.

Usage:
    python3 spawn_and_track.py --agent FORGE --ticket ACE-xxx --model sonnet --task "brief description"
"""

import argparse
import fcntl
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
ACTIVITY_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/agent-activity.json")


def get_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def write_to_audit_log(agent: str, ticket: str, model: str, task: str) -> None:
    """Insert spawn record into audit_log table."""
    detail = json.dumps({"ticket": ticket, "model": model, "task": task})
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO audit_log (agent, action, ticket_id, detail, model) VALUES (?, 'spawn', ?, ?, ?)",
            (agent, ticket, detail, model),
        )
        conn.commit()
    finally:
        conn.close()


def update_activity_json(agent: str, ticket: str, model: str, task: str) -> str:
    """Atomically add a worker entry to agent-activity.json. Returns the new worker id."""
    worker_id = str(uuid.uuid4())[:8]
    started_at = get_now_iso()
    tmp_path = Path(str(ACTIVITY_PATH) + ".tmp")

    # Lock the .tmp file for atomic write, then rename into place
    with open(tmp_path, "w") as tmp_file:
        fcntl.flock(tmp_file.fileno(), fcntl.LOCK_EX)
        try:
            # Read current state
            if ACTIVITY_PATH.exists():
                with open(ACTIVITY_PATH, "r") as f:
                    data = json.load(f)
            else:
                data = {"ts": started_at, "agents": {}, "workers": [], "sync": {}, "emergency": {}}

            # Ensure workers list exists
            if "workers" not in data or not isinstance(data["workers"], list):
                data["workers"] = []

            # Append new worker entry
            data["workers"].append({
                "id": worker_id,
                "agent": agent,
                "ticket": ticket,
                "model": model,
                "task": task,
                "started_at": started_at,
                "status": "running",
            })

            # Write to tmp file (already open and locked)
            tmp_file.seek(0)
            json.dump(data, tmp_file, indent=2)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        finally:
            fcntl.flock(tmp_file.fileno(), fcntl.LOCK_UN)

    # Atomic rename
    os.rename(tmp_path, ACTIVITY_PATH)
    return worker_id


def main():
    parser = argparse.ArgumentParser(description="Track agent spawn in audit_log and agent-activity.json")
    parser.add_argument("--agent", required=True, help="Agent name (e.g. FORGE)")
    parser.add_argument("--ticket", required=True, help="Ticket ID (e.g. ACE-123)")
    parser.add_argument("--model", required=True, help="Model name (e.g. sonnet)")
    parser.add_argument("--task", required=True, help="Brief task description")
    args = parser.parse_args()

    # Write to audit_log
    write_to_audit_log(args.agent, args.ticket, args.model, args.task)

    # Update activity JSON atomically
    worker_id = update_activity_json(args.agent, args.ticket, args.model, args.task)

    print(f"SPAWN_TRACKED: agent={args.agent} ticket={args.ticket} model={args.model} worker_id={worker_id}")


if __name__ == "__main__":
    main()
