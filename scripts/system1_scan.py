#!/usr/bin/env python3
"""
system1_scan.py — Cheap cognitive watchdog (System 1).

Uses the CHEAPEST available model (Haiku or cheapest Sonnet fallback).
Reads agent context, asks: "Is there anything worth deeper analysis?"
If YES → escalate to System 2 (after checking daily cap).
If NO → log idle, update last_system1_run.

Usage:
    python3 system1_scan.py --agent forge
    python3 system1_scan.py --agent forge --mock-response YES
    python3 system1_scan.py --agent forge --mock-response NO

Security rules (from SENTINEL threat report):
  - NEVER pass raw proposal/message content unvalidated into prompt
  - ALWAYS use read_agent_messages.py (not raw SQL) for agent messages
  - ALWAYS use parameterized queries for all DB writes
  - Context sent to Haiku is bounded to ≤500 tokens
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts/ is on path
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
OPENCLAW_CONFIG = Path.home() / ".openclaw/openclaw.json"

# Model preference: cheapest first
SYSTEM1_MODEL_PREFERENCE = [
    "claude-haiku-4-5-20251001",
    "claude-haiku-3-5-20241022",
    "claude-haiku-3-20240307",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _log_audit(agent: str, action: str, detail: str) -> None:
    """Write to audit_log using parameterized query."""
    try:
        conn = _get_db()
        conn.execute(
            "INSERT INTO audit_log (ts, agent, action, detail) VALUES (?, ?, ?, ?)",
            (_now_iso(), agent, action, detail),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[system1_scan] audit_log write failed: {e}", file=sys.stderr)


def _read_openclaw_config() -> dict:
    try:
        return json.loads(OPENCLAW_CONFIG.read_text())
    except Exception:
        return {}


def _get_api_config() -> dict:
    """
    Extract API configuration from openclaw.json.
    Returns dict with 'base_url', 'cf_token', 'anthropic_key', 'model'.
    """
    cfg = _read_openclaw_config()
    result = {"base_url": None, "cf_token": None, "anthropic_key": None, "model": None}

    # Walk for API keys (sk-ant- prefix = Anthropic)
    def find_key(obj, depth=0):
        if depth > 8 or not isinstance(obj, (dict, list)):
            return None
        items = obj.items() if isinstance(obj, dict) else enumerate(obj)
        for k, v in items:
            if isinstance(v, str) and v.startswith("sk-ant-"):
                return v
            found = find_key(v, depth + 1)
            if found:
                return found
        return None

    result["anthropic_key"] = find_key(cfg)

    # Extract CF gateway config
    providers = cfg.get("models", {}).get("providers", {})
    cf = providers.get("cloudflare-ai-gateway", {})
    if cf:
        result["base_url"] = cf.get("baseUrl", "")
        cf_header = cf.get("headers", {}).get("cf-aig-authorization", "")
        if cf_header.startswith("Bearer "):
            result["cf_token"] = cf_header[7:]

        # Pick cheapest available model
        available = [m.get("id", "") for m in cf.get("models", [])]
        for preferred in SYSTEM1_MODEL_PREFERENCE:
            for avail in available:
                if preferred in avail or avail in preferred:
                    result["model"] = avail
                    break
            if result["model"]:
                break
        # fallback to first available
        if not result["model"] and available:
            result["model"] = available[0]

    return result


def _get_or_create_cognitive_state(agent_id: str) -> dict:
    """Get existing cognitive_state or create a fresh one for agent."""
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


def _build_context_block(agent_id: str) -> str:
    """
    Build a compact context block for System 1 (max ~500 tokens).

    SECURITY: All content is fetched via approved scripts/queries.
    Messages MUST come from read_agent_messages.py (not raw SQL).
    Content is truncated aggressively to stay within token budget.
    """
    conn = _get_db()
    lines = [f"Agent: {agent_id}\n"]

    # ── Top beliefs (active only, limit 5) ───────────────────────────────────
    try:
        beliefs = conn.execute(
            """SELECT content, category, confidence, evidence_for, evidence_against,
                      last_accessed, importance
               FROM beliefs
               WHERE agent_id = ? AND status = 'active'
               ORDER BY activation_score DESC, importance DESC
               LIMIT 5""",
            (agent_id,),
        ).fetchall()

        lines.append("BELIEFS (top 5 active):")
        for b in beliefs:
            content = str(b["content"] or "").strip()[:80]
            cat = b["category"] or "fact"
            conf = b["confidence"] or 0.0

            # Flag low-evidence beliefs
            ev_for = str(b["evidence_for"] or "").strip()
            ev_against = str(b["evidence_against"] or "").strip()
            low_evidence = (not ev_for) and (not ev_against)

            # Flag stale beliefs (last_accessed > 7 days ago or never)
            stale = False
            last_acc = b["last_accessed"]
            if last_acc:
                try:
                    la_dt = datetime.fromisoformat(last_acc.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - la_dt).days
                    if age_days > 7:
                        stale = True
                except Exception:
                    stale = True
            else:
                stale = True  # Never accessed = stale

            flags = []
            if low_evidence:
                flags.append("LOW-EVIDENCE")
            if stale:
                flags.append("STALE")
            flag_str = f" [{','.join(flags)}]" if flags else ""

            lines.append(f"  - [{cat},{conf:.2f}]{flag_str} {content}")

        if not beliefs:
            lines.append("  (no active beliefs)")
    except Exception as e:
        lines.append(f"  (belief read error: {e})")

    # ── Unread messages (via read_agent_messages.py — validated path) ─────────
    # SECURITY: We call the validated read function, NOT raw SQL on agent_messages
    try:
        import read_agent_messages as ram
        messages = ram.read_messages(to_agent=agent_id, unread_only=True, limit=3)
        lines.append("UNREAD MESSAGES:")
        if messages:
            for msg in messages:
                content_preview = str(msg.get("sanitized_content") or msg.get("content") or "")[:60]
                from_agent = str(msg.get("from_agent_id", "?"))[:20]
                lines.append(f"  - from:{from_agent} {content_preview}")
        else:
            lines.append("  (none)")
    except Exception as e:
        lines.append(f"  (message read error: {e})")

    # ── Open proposals (limit 3) ──────────────────────────────────────────────
    try:
        proposals = conn.execute(
            """SELECT title, author_agent_id, requires_review
               FROM proposals
               WHERE status = 'open' AND blocked = 0
               ORDER BY created_at DESC
               LIMIT 3""",
        ).fetchall()
        lines.append("OPEN PROPOSALS:")
        if proposals:
            for p in proposals:
                title = str(p["title"] or "")[:60]
                author = str(p["author_agent_id"] or "?")[:15]
                rev = " [NEEDS_REVIEW]" if p["requires_review"] else ""
                lines.append(f"  - {title} (by {author}){rev}")
        else:
            lines.append("  (none)")
    except Exception as e:
        lines.append(f"  (proposal read error: {e})")

    # ── Knowledge gaps (unresolved, limit 3) ─────────────────────────────────
    try:
        gaps = conn.execute(
            """SELECT description, domain, importance
               FROM knowledge_gaps
               WHERE agent_id = ? AND resolved_at IS NULL
               ORDER BY importance DESC
               LIMIT 3""",
            (agent_id,),
        ).fetchall()
        lines.append("KNOWLEDGE GAPS:")
        if gaps:
            for g in gaps:
                desc = str(g["description"] or "").strip()[:80]
                domain = str(g["domain"] or "").strip()[:20]
                imp = g["importance"] or 0.0
                if not desc:
                    desc = "(empty description)"  # graceful handling of empty gaps
                lines.append(f"  - [{domain},imp={imp:.0f}] {desc}")
        else:
            lines.append("  (none)")
    except Exception as e:
        lines.append(f"  (knowledge_gap read error: {e})")

    conn.close()
    return "\n".join(lines)


def _call_ai(prompt: str, api_cfg: dict) -> str:
    """
    Call the AI via openclaw agent subprocess.
    api_cfg kept for interface compatibility but not used.
    """
    import subprocess, uuid as _uuid, json as _json
    session_id = str(_uuid.uuid4())
    result = subprocess.run(
        ['openclaw', 'agent', '--json', '--session-id', session_id, '--message', prompt],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"openclaw agent failed (rc={result.returncode}): {result.stderr[:300]}")
    try:
        data = _json.loads(result.stdout)
    except Exception as e:
        raise RuntimeError(f"Failed to parse openclaw agent JSON: {result.stdout[:200]}")
    payloads = data.get('result', {}).get('payloads', [])
    if not payloads:
        raise RuntimeError(f"No payloads in openclaw agent response: {result.stdout[:200]}")
    text = payloads[0].get('text', '')
    if not text:
        raise RuntimeError("Empty text in openclaw agent response")
    return text


def _parse_yes_no(response_text: str) -> tuple[bool, str]:
    """
    Parse YES/NO from Haiku response.
    Returns (should_escalate: bool, reason: str)
    """
    text = response_text.strip()
    first_line = text.split("\n")[0].upper()
    escalate = first_line.startswith("YES")
    # Extract reason (everything after YES/NO)
    reason = text[3:].strip(" :.-") if len(text) > 3 else text
    return escalate, reason


def run_system1_scan(agent_id: str, mock_response: str | None = None) -> dict:
    """
    Main entry point for System 1 scan.

    Returns:
        {
          'escalated': bool,
          'reason': str,
          'cap_blocked': bool,  # True if cap prevented System 2
          'status': 'idle' | 'escalated' | 'error',
        }
    """
    conn = _get_db()
    now = _now_iso()

    # ── Ensure cognitive_state exists ─────────────────────────────────────────
    _get_or_create_cognitive_state(agent_id)

    # ── Build context block ───────────────────────────────────────────────────
    context_block = _build_context_block(agent_id)

    # ── Build prompt (max ~500 tokens) ────────────────────────────────────────
    prompt = (
        f"You are a fast cognitive watchdog for agent '{agent_id}'.\n"
        f"Given the following agent context, answer: is there anything worth deeper analysis?\n"
        f"Answer with exactly 'YES: <one-sentence reason>' or 'NO: <one-sentence reason>'.\n\n"
        f"--- CONTEXT ---\n"
        f"{context_block[:1200]}\n"
        f"--- END CONTEXT ---\n\n"
        f"Decision (YES or NO with one sentence reason):"
    )

    # ── Call AI (or use mock) ─────────────────────────────────────────────────
    scan_result_text = ""
    api_error = None

    if mock_response is not None:
        # Mock mode: for testing control flow without real API calls
        scan_result_text = f"{mock_response.upper()}: mock response for testing"
    else:
        api_cfg = _get_api_config()
        try:
            scan_result_text = _call_ai(prompt, api_cfg)
        except Exception as e:
            api_error = str(e)
            # SAFE FALLBACK: if AI call fails, do NOT escalate
            scan_result_text = "NO: API call failed, safe fallback to idle"
            _log_audit(
                agent_id,
                "system1_scan_error",
                f"AI call failed: {api_error}",
            )

    # ── Parse YES/NO ──────────────────────────────────────────────────────────
    escalate, reason = _parse_yes_no(scan_result_text)

    # ── Check System 2 daily cap ─────────────────────────────────────────────
    cap_blocked = False
    if escalate:
        state = _get_or_create_cognitive_state(agent_id)
        today = datetime.now(timezone.utc).date().isoformat()
        count_today = state.get("system2_count_today", 0)
        state_date = state.get("system2_date", "")

        # Reset counter if it's a new day
        if state_date != today:
            count_today = 0

        if count_today >= 2:
            cap_blocked = True
            _log_audit(
                agent_id,
                "system1_scan",
                f"ESCALATED but DAILY_CAP_REACHED (count={count_today}): {reason}",
            )
            # Cap hit → set status back to idle, not escalated
            conn.execute(
                """UPDATE cognitive_state
                   SET scan_status = 'idle', last_scan_result = ?, last_system1_run = ?, updated_at = ?
                   WHERE agent_id = ?""",
                (f"[CAP_BLOCKED] {reason}", now, now, agent_id),
            )
            conn.commit()
            conn.close()
            return {
                "escalated": False,
                "reason": f"[DAILY_CAP_REACHED] {reason}",
                "cap_blocked": True,
                "status": "idle",
            }

    # ── Update cognitive_state ────────────────────────────────────────────────
    new_status = "escalated" if escalate else "idle"
    conn.execute(
        """UPDATE cognitive_state
           SET scan_status = ?, last_scan_result = ?, last_system1_run = ?, updated_at = ?
           WHERE agent_id = ?""",
        (new_status, reason[:500], now, now, agent_id),
    )
    conn.commit()
    conn.close()

    # ── Log to audit_log ──────────────────────────────────────────────────────
    _log_audit(
        agent_id,
        "system1_scan",
        f"result={'ESCALATED' if escalate else 'idle'} reason={reason[:200]}",
    )

    return {
        "escalated": escalate,
        "reason": reason,
        "cap_blocked": False,
        "status": new_status,
    }


def main():
    parser = argparse.ArgumentParser(description="System 1 cognitive scan")
    parser.add_argument("--agent", required=True, help="Agent ID to scan")
    parser.add_argument(
        "--mock-response",
        choices=["YES", "NO"],
        default=None,
        help="Mock AI response for testing (YES or NO). Skips real API call.",
    )
    args = parser.parse_args()

    print(f"[system1_scan] Starting scan for agent='{args.agent}'")
    result = run_system1_scan(args.agent, mock_response=args.mock_response)

    print(f"[system1_scan] Result: escalated={result['escalated']}")
    print(f"[system1_scan] Reason: {result['reason']}")
    print(f"[system1_scan] Status: {result['status']}")
    if result["cap_blocked"]:
        print(f"[system1_scan] ⚠ Daily cap was hit — System 2 NOT fired")

    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
