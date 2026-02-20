#!/usr/bin/env python3
"""
cognitive_loop.py — Cognitive loop orchestrator.

Coordinates System 1 (cheap scan) and System 2 (deliberate reasoning).
Prevents double-firing, checks WAL size, enforces daily cap.

Usage:
    python3 cognitive_loop.py --agent forge
    python3 cognitive_loop.py --agent forge --mock-s1 YES  # Force System 1 to YES
    python3 cognitive_loop.py --agent forge --mock-s1 NO   # Force System 1 to NO

Safety gates (from SENTINEL threat report):
  1. Double-fire prevention: exits if scan_status='running'
  2. WAL size check: halts if vector.db-wal > 50MB
  3. System 2 daily cap: enforced in system2_think.py, also checked here
  4. All DB writes use parameterized queries
"""

import argparse
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
WAL_PATH = DB_PATH.parent / "vector.db-wal"
WAL_SIZE_LIMIT_MB = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _log_audit(agent: str, action: str, detail: str) -> None:
    try:
        conn = _get_db()
        conn.execute(
            "INSERT INTO audit_log (ts, agent, action, detail) VALUES (?, ?, ?, ?)",
            (_now_iso(), agent, action, detail),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[cognitive_loop] audit_log write failed: {e}", file=sys.stderr)


def _ensure_db_permissions() -> None:
    """
    SECURITY: Ensure the DB file and WAL/SHM siblings are owner-only (mode 0o600).
    Any broader permissions allow other processes on this machine to read all beliefs,
    memories, and agent messages.

    Called at startup by cognitive_loop. Auto-fixes if wrong.
    """
    import stat

    db_files = [DB_PATH, DB_PATH.parent / "vector.db-wal", DB_PATH.parent / "vector.db-shm"]
    for p in db_files:
        if not p.exists():
            continue
        current_mode = p.stat().st_mode & 0o777
        if current_mode != 0o600:
            try:
                os.chmod(p, 0o600)
                print(
                    f"[cognitive_loop] 🔒 Fixed DB permissions: {p.name} "
                    f"({oct(current_mode)} → 0o600)",
                    file=sys.stderr,
                )
                _log_audit(
                    "vector",
                    "db_permission_fix",
                    f"Auto-fixed {p.name}: mode was {oct(current_mode)}, corrected to 0o600",
                )
            except Exception as e:
                print(
                    f"[cognitive_loop] ❌ Could not fix permissions on {p.name}: {e}",
                    file=sys.stderr,
                )


def _check_wal_size() -> tuple[bool, float]:
    """
    Check WAL file size.
    Returns (exceeds_limit: bool, size_mb: float).
    """
    if not WAL_PATH.exists():
        return False, 0.0

    size_bytes = WAL_PATH.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    return size_mb > WAL_SIZE_LIMIT_MB, size_mb


def _get_or_create_cognitive_state(agent_id: str) -> dict:
    """Create cognitive_state row if it doesn't exist."""
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM cognitive_state WHERE agent_id = ?", (agent_id,)
    ).fetchone()

    if row is None:
        state_id = f"cog-{agent_id[:8]}-{uuid.uuid4().hex[:8]}"
        now = _now_iso()
        conn.execute(
            """INSERT INTO cognitive_state
               (id, agent_id, last_system1_run, last_system2_run,
                system2_count_today, system2_date, pending_intentions,
                last_scan_result, scan_status, created_at, updated_at)
               VALUES (?, ?, NULL, NULL, 0, NULL, '[]', NULL, 'idle', ?, ?)""",
            (state_id, agent_id, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM cognitive_state WHERE agent_id = ?", (agent_id,)
        ).fetchone()

    result = dict(row)
    conn.close()
    return result


def _set_scan_status(agent_id: str, status: str) -> None:
    """Update scan_status for an agent using parameterized query."""
    conn = _get_db()
    conn.execute(
        "UPDATE cognitive_state SET scan_status = ?, updated_at = ? WHERE agent_id = ?",
        (status, _now_iso(), agent_id),
    )
    conn.commit()
    conn.close()


def _get_system2_cap_status(agent_id: str) -> tuple[bool, int]:
    """
    Check System 2 daily cap without triggering System 2.
    Returns (cap_hit: bool, count_today: int).
    """
    from datetime import date
    today = date.today().isoformat()

    conn = _get_db()
    row = conn.execute(
        "SELECT system2_count_today, system2_date FROM cognitive_state WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    conn.close()

    if not row:
        return False, 0

    count = row["system2_count_today"] or 0
    state_date = row["system2_date"] or ""

    if state_date != today:
        return False, 0  # New day, counter reset

    return count >= 2, count


def run_cognitive_loop(agent_id: str, mock_s1: str | None = None) -> dict:
    """
    Main cognitive loop orchestrator.

    Returns:
        {
          'completed': bool,
          'exit_reason': str,    # 'already_running'|'wal_too_large'|'system1_idle'|
                                 #  'system1_escalated_cap'|'system1_escalated_ran_s2'
          'system1_escalated': bool,
          'system2_ran': bool,
        }
    """
    now = _now_iso()

    # ── GATE -1: DB permissions check (security hardening) ───────────────────
    _ensure_db_permissions()

    # ── GATE 0: WAL size check ────────────────────────────────────────────────
    # Check BEFORE taking any lock or running anything.
    # If WAL is too large, we could corrupt the DB with more writes.
    wal_too_large, wal_mb = _check_wal_size()
    if wal_too_large:
        _log_audit(
            agent_id,
            "cognitive_loop_wal_alert",
            f"WAL file too large: {wal_mb:.1f}MB > {WAL_SIZE_LIMIT_MB}MB limit. "
            f"Cognitive loop HALTED. Manual WAL checkpoint required.",
        )
        print(
            f"[cognitive_loop] ⚠ WAL file too large: {wal_mb:.1f}MB > {WAL_SIZE_LIMIT_MB}MB\n"
            f"[cognitive_loop] HALTING — manual checkpoint required (PRAGMA wal_checkpoint)"
        )
        return {
            "completed": False,
            "exit_reason": "wal_too_large",
            "system1_escalated": False,
            "system2_ran": False,
        }

    # ── GATE 1: Ensure cognitive_state exists ─────────────────────────────────
    state = _get_or_create_cognitive_state(agent_id)

    # ── GATE 2: Double-fire prevention ────────────────────────────────────────
    # If scan_status='running', another instance is already executing.
    # Exit immediately to prevent concurrent writes.
    current_status = state.get("scan_status", "idle")
    if current_status == "running":
        print(
            f"[cognitive_loop] ⚠ scan_status='running' — already in progress. Exiting."
        )
        _log_audit(
            agent_id,
            "cognitive_loop_double_fire",
            f"Prevented double-fire: scan_status=running for agent={agent_id}",
        )
        return {
            "completed": False,
            "exit_reason": "already_running",
            "system1_escalated": False,
            "system2_ran": False,
        }

    # ── Set status to 'running' ───────────────────────────────────────────────
    _set_scan_status(agent_id, "running")
    print(f"[cognitive_loop] Started for agent='{agent_id}' (scan_status=running)")

    try:
        # ── STEP 1: Run System 1 scan ─────────────────────────────────────────
        from system1_scan import run_system1_scan
        s1_result = run_system1_scan(agent_id, mock_response=mock_s1)

        escalated = s1_result.get("escalated", False)
        cap_blocked = s1_result.get("cap_blocked", False)
        s1_reason = s1_result.get("reason", "")

        print(f"[cognitive_loop] System 1: escalated={escalated} reason='{s1_reason[:60]}'")

        # ── STEP 2: Fire System 2 if escalated and cap not hit ────────────────
        system2_ran = False

        if escalated and not cap_blocked:
            # Double-check cap from loop's perspective (defense in depth)
            cap_hit, count = _get_system2_cap_status(agent_id)
            if cap_hit:
                print(
                    f"[cognitive_loop] System 2 cap hit ({count}/2 today) — not firing"
                )
                _log_audit(
                    agent_id,
                    "cognitive_loop",
                    f"System 1 escalated but System 2 cap hit ({count}/2): {s1_reason[:100]}",
                )
            else:
                print(f"[cognitive_loop] System 1 escalated → firing System 2")
                from system2_think import run_system2_think
                mock_s2 = None
                if mock_s1 is not None:
                    # In mock mode, provide a minimal mock S2 output to avoid API calls
                    mock_s2 = '{"type": "knowledge_gap", "domain": "mock", "description": "Mock System 2 output for test", "importance": 3.0}'

                s2_result = run_system2_think(agent_id, s1_reason, mock_output=mock_s2)
                system2_ran = s2_result.get("success", False)
                print(
                    f"[cognitive_loop] System 2: success={system2_ran} "
                    f"type={s2_result.get('action_type')}"
                )
        elif escalated and cap_blocked:
            print(f"[cognitive_loop] System 1 escalated but daily cap was already hit by scan")

        # ── STEP 3: Reset scan_status ─────────────────────────────────────────
        # system1_scan already set status to 'idle' or 'escalated'
        # system2_think sets it back to 'idle' on completion
        # If S1 escalated but S2 didn't run (cap hit), reset to idle here
        if escalated and not system2_ran:
            _set_scan_status(agent_id, "idle")

        _log_audit(
            agent_id,
            "cognitive_loop",
            f"complete: escalated={escalated} s2_ran={system2_ran} "
            f"wal_mb={wal_mb:.1f}",
        )

        exit_reason = "system1_idle"
        if escalated and system2_ran:
            exit_reason = "system1_escalated_ran_s2"
        elif escalated and not system2_ran:
            exit_reason = "system1_escalated_cap"

        return {
            "completed": True,
            "exit_reason": exit_reason,
            "system1_escalated": escalated,
            "system2_ran": system2_ran,
        }

    except Exception as e:
        # On any error, reset scan_status to 'error' so we don't get stuck
        _set_scan_status(agent_id, "error")
        _log_audit(
            agent_id,
            "cognitive_loop_error",
            f"Unhandled exception: {str(e)[:200]}",
        )
        print(f"[cognitive_loop] ❌ Error: {e}", file=sys.stderr)
        return {
            "completed": False,
            "exit_reason": f"error: {e}",
            "system1_escalated": False,
            "system2_ran": False,
        }

    finally:
        # Ensure we never leave status='running' on exception
        try:
            conn = _get_db()
            row = conn.execute(
                "SELECT scan_status FROM cognitive_state WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            conn.close()
            if row and row["scan_status"] == "running":
                _set_scan_status(agent_id, "idle")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Cognitive loop orchestrator")
    parser.add_argument("--agent", required=True, help="Agent ID to run cognitive loop for")
    parser.add_argument(
        "--mock-s1",
        choices=["YES", "NO"],
        default=None,
        help="Mock System 1 response (YES=escalate, NO=idle). Skips real API call.",
    )
    args = parser.parse_args()

    result = run_cognitive_loop(args.agent, mock_s1=args.mock_s1)

    print(f"\n[cognitive_loop] Exit reason: {result['exit_reason']}")
    print(f"[cognitive_loop] S1 escalated: {result['system1_escalated']}")
    print(f"[cognitive_loop] S2 ran: {result['system2_ran']}")

    return 0 if result["completed"] else 1


if __name__ == "__main__":
    sys.exit(main())
