#!/usr/bin/env python3
"""
check_budget.py — Phase 0 pre-spawn circuit breaker
VECTOR calls this BEFORE every agent/worker spawn.

Usage:
    python3 check_budget.py --agent FORGE --model sonnet
    
Exit codes:
    0 = approved to spawn
    1 = agent limit exceeded (agent spend > $10/day OR agent spawn count > 20 today)
    2 = system hard stop (system spend > $30/day)
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")

# Limits
AGENT_COST_LIMIT_USD = 10.0
SYSTEM_COST_LIMIT_USD = 30.0
AGENT_SPAWN_LIMIT = 20


def get_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_budget(agent: str, model: str) -> int:
    """
    Check budget and spawn rate limits.
    Returns: 0 (ok), 1 (agent blocked), 2 (system hard stop)
    """
    today = get_today_str()

    conn = sqlite3.connect(DB_PATH)
    try:
        # --- Check system total spend today ---
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_tracking WHERE date = ?",
            (today,),
        ).fetchone()
        system_total = float(row[0]) if row else 0.0

        if system_total > SYSTEM_COST_LIMIT_USD:
            print(
                f"BUDGET_BLOCKED [EXIT 2]: System daily spend ${system_total:.4f} exceeds hard limit "
                f"${SYSTEM_COST_LIMIT_USD:.2f}. ALL spawns halted."
            )
            return 2

        # --- Check agent spend today ---
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_tracking WHERE date = ? AND agent = ?",
            (today, agent.lower()),
        ).fetchone()
        agent_total = float(row[0]) if row else 0.0

        if agent_total > AGENT_COST_LIMIT_USD:
            print(
                f"BUDGET_BLOCKED [EXIT 1]: Agent {agent} daily spend ${agent_total:.4f} exceeds per-agent limit "
                f"${AGENT_COST_LIMIT_USD:.2f}. Spawn blocked."
            )
            return 1

        # --- Check agent spawn count today (rate limiting for Max OAuth / zero-cost plans) ---
        row = conn.execute(
            """
            SELECT COUNT(*) FROM audit_log
            WHERE agent = ?
              AND action = 'spawn'
              AND date(ts) = ?
            """,
            (agent, today),
        ).fetchone()
        spawn_count = int(row[0]) if row else 0

        if spawn_count >= AGENT_SPAWN_LIMIT:
            print(
                f"BUDGET_BLOCKED [EXIT 1]: Agent {agent} has spawned {spawn_count} times today "
                f"(limit: {AGENT_SPAWN_LIMIT}). Spawn rate limit hit."
            )
            return 1

        # --- All clear ---
        print(
            f"BUDGET_OK [EXIT 0]: agent={agent} model={model} | "
            f"agent_spend_today=${agent_total:.4f} (limit ${AGENT_COST_LIMIT_USD:.2f}) | "
            f"system_spend_today=${system_total:.4f} (limit ${SYSTEM_COST_LIMIT_USD:.2f}) | "
            f"agent_spawns_today={spawn_count} (limit {AGENT_SPAWN_LIMIT})"
        )
        return 0

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Pre-spawn budget circuit breaker")
    parser.add_argument("--agent", required=True, help="Agent name (e.g. FORGE)")
    parser.add_argument("--model", required=True, help="Model name (e.g. sonnet)")
    args = parser.parse_args()

    exit_code = check_budget(args.agent, args.model)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
