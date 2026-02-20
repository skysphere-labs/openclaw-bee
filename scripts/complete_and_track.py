#!/usr/bin/env python3
"""
complete_and_track.py — Phase 0 completion tracking
Records PM/worker completion into audit_log (SQLite) and updates agent-activity.json atomically.

Usage:
    python3 complete_and_track.py --agent FORGE --ticket ACE-xxx --status done --cost 0.00 --summary "brief"
    python3 complete_and_track.py --agent FORGE --ticket ACE-xxx --status failed --cost 0.00 --summary "what went wrong"
"""

import argparse
import fcntl
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
ACTIVITY_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/agent-activity.json")


def get_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def write_completion_to_audit_log(agent: str, ticket: str, status: str, cost: float, summary: str) -> None:
    """Insert completion record into audit_log table."""
    action = "complete" if status == "done" else "failed"
    detail = json.dumps({"ticket": ticket, "status": status, "cost_usd": cost, "summary": summary})
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO audit_log (agent, action, ticket_id, detail, cost_usd) VALUES (?, ?, ?, ?, ?)",
            (agent, action, ticket, detail, cost),
        )
        conn.commit()
    finally:
        conn.close()


def update_activity_json_completion(agent: str, ticket: str, status: str, summary: str) -> None:
    """Atomically update the most-recent running worker for this agent+ticket in agent-activity.json."""
    completed_at = get_now_iso()
    tmp_path = Path(str(ACTIVITY_PATH) + ".tmp")

    with open(tmp_path, "w") as tmp_file:
        fcntl.flock(tmp_file.fileno(), fcntl.LOCK_EX)
        try:
            if ACTIVITY_PATH.exists():
                with open(ACTIVITY_PATH, "r") as f:
                    data = json.load(f)
            else:
                data = {"ts": completed_at, "agents": {}, "workers": [], "sync": {}, "emergency": {}}

            if "workers" not in data or not isinstance(data["workers"], list):
                data["workers"] = []

            # Find the most recent running worker for this agent+ticket and update it
            updated = False
            for worker in reversed(data["workers"]):
                if worker.get("agent") == agent and worker.get("ticket") == ticket and worker.get("status") == "running":
                    worker["status"] = status  # "done" or "failed"
                    worker["completed_at"] = completed_at
                    worker["summary"] = summary
                    updated = True
                    break

            # If no running worker found, append a completion record
            if not updated:
                data["workers"].append({
                    "agent": agent,
                    "ticket": ticket,
                    "status": status,
                    "completed_at": completed_at,
                    "summary": summary,
                })

            tmp_file.seek(0)
            json.dump(data, tmp_file, indent=2)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        finally:
            fcntl.flock(tmp_file.fileno(), fcntl.LOCK_UN)

    os.rename(tmp_path, ACTIVITY_PATH)


def main():
    parser = argparse.ArgumentParser(description="Track agent completion in audit_log and agent-activity.json")
    parser.add_argument("--agent", required=True, help="Agent name (e.g. FORGE)")
    parser.add_argument("--ticket", required=True, help="Ticket ID (e.g. ACE-123)")
    parser.add_argument("--status", required=True, choices=["done", "failed"], help="Completion status")
    parser.add_argument("--cost", required=True, type=float, help="Cost in USD (e.g. 0.00)")
    parser.add_argument("--summary", required=True, help="Brief summary of what was done or what failed")
    args = parser.parse_args()

    # Write completion to audit_log
    write_completion_to_audit_log(args.agent, args.ticket, args.status, args.cost, args.summary)

    # Update activity JSON atomically
    update_activity_json_completion(args.agent, args.ticket, args.status, args.summary)

    print(f"COMPLETE_TRACKED: agent={args.agent} ticket={args.ticket} status={args.status} cost=${args.cost:.4f}")


if __name__ == "__main__":
    main()
