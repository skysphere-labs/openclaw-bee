#!/usr/bin/env python3
"""
system2_think.py — Deliberate reasoning (System 2).

Uses Sonnet. Fires ONLY when System 1 escalates AND daily cap not hit.
Generates ONE action: a proposal, a belief update, or a knowledge_gap update.

Usage:
    python3 system2_think.py --agent forge --reason "System 1 escalated: new proposal"
    python3 system2_think.py --agent forge --reason "test" --mock-output '{"type":"proposal",...}'

Security rules:
  - Check daily cap FIRST (before any expensive computation)
  - Only inject agent's OWN beliefs (no cross-PM contamination)
  - Parameterized queries for all DB writes
  - Mock mode for testing without real API calls

Daily cap: MAX 2 per agent per day (enforced in code, not just prompt).
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
OPENCLAW_CONFIG = Path.home() / ".openclaw/openclaw.json"

SYSTEM2_MODEL = "claude-sonnet-4-6"
DAILY_CAP = 2  # Hard cap: enforced in code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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
        print(f"[system2_think] audit_log write failed: {e}", file=sys.stderr)


def _get_cognitive_state(agent_id: str) -> dict | None:
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM cognitive_state WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _check_daily_cap(agent_id: str) -> tuple[bool, int]:
    """
    Check if agent has hit the daily System 2 cap.
    Returns (cap_hit: bool, count_today: int).
    Resets counter if it's a new day.
    """
    state = _get_cognitive_state(agent_id)
    if not state:
        return False, 0

    today = _today_iso()
    count = state.get("system2_count_today", 0)
    state_date = state.get("system2_date", "")

    # New day → reset counter in DB
    if state_date != today:
        conn = _get_db()
        conn.execute(
            """UPDATE cognitive_state
               SET system2_count_today = 0, system2_date = ?, updated_at = ?
               WHERE agent_id = ?""",
            (today, _now_iso(), agent_id),
        )
        conn.commit()
        conn.close()
        return False, 0

    return count >= DAILY_CAP, count


def _build_rich_context(agent_id: str) -> str:
    """
    Build rich context for System 2 reasoning.
    SECURITY: Only injects THIS agent's beliefs (agent_id filter enforced).
    Never injects other PMs' beliefs.
    """
    conn = _get_db()
    lines = [f"=== System 2 Context for agent: {agent_id} ===\n"]

    # ── Agent beliefs (OWN ONLY — critical isolation) ─────────────────────────
    # SECURITY: WHERE agent_id = ? ensures we ONLY get this agent's beliefs
    # No cross-PM contamination possible via this query.
    try:
        beliefs = conn.execute(
            """SELECT content, category, confidence, action_implication,
                      evidence_for, evidence_against, importance
               FROM beliefs
               WHERE agent_id = ? AND status = 'active'
               ORDER BY activation_score DESC, importance DESC
               LIMIT 10""",
            (agent_id,),  # PARAMETERIZED — isolation enforced here
        ).fetchall()

        lines.append(f"## My beliefs (agent_id='{agent_id}' ONLY):")
        for b in beliefs:
            content = str(b["content"] or "")[:120]
            cat = b["category"] or "fact"
            conf = b["confidence"] or 0.0
            lines.append(f"  - [{cat},{conf:.2f}] {content}")
        if not beliefs:
            lines.append("  (no active beliefs)")
    except Exception as e:
        lines.append(f"  (belief read error: {e})")

    # ── Relevant memories (via retrieve_memories.py) ──────────────────────────
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "retrieve_memories.py"),
             "--agent", agent_id, "--limit", "5"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            lines.append("\n## Relevant memories:")
            for line in result.stdout.strip().split("\n")[:5]:
                lines.append(f"  {line[:120]}")
    except Exception:
        pass  # Memory retrieval failure is non-fatal

    # ── Open proposals (for context, not for injection) ────────────────────────
    try:
        proposals = conn.execute(
            """SELECT title, content, author_agent_id, requires_review
               FROM proposals
               WHERE status = 'open' AND blocked = 0
               ORDER BY created_at DESC
               LIMIT 3""",
        ).fetchall()
        lines.append("\n## Open proposals (shared context):")
        for p in proposals:
            title = str(p["title"] or "")[:80]
            author = str(p["author_agent_id"] or "?")[:15]
            lines.append(f"  - '{title}' by {author}")
        if not proposals:
            lines.append("  (none)")
    except Exception as e:
        lines.append(f"  (proposal read error: {e})")

    # ── Unread messages (via validated read path) ─────────────────────────────
    try:
        import read_agent_messages as ram
        messages = ram.read_messages(to_agent=agent_id, unread_only=True, limit=3)
        lines.append("\n## Unread messages:")
        if messages:
            for msg in messages:
                content_preview = str(msg.get("sanitized_content") or msg.get("content") or "")[:80]
                from_agent = str(msg.get("from_agent_id", "?"))[:15]
                lines.append(f"  - from:{from_agent} {content_preview}")
        else:
            lines.append("  (none)")
    except Exception as e:
        lines.append(f"  (message read error: {e})")

    # ── Knowledge gaps ────────────────────────────────────────────────────────
    try:
        gaps = conn.execute(
            """SELECT description, domain, importance
               FROM knowledge_gaps
               WHERE agent_id = ? AND resolved_at IS NULL
               ORDER BY importance DESC
               LIMIT 3""",
            (agent_id,),
        ).fetchall()
        lines.append("\n## Knowledge gaps:")
        for g in gaps:
            desc = str(g["description"] or "").strip()[:100]
            domain = str(g["domain"] or "").strip()[:20]
            imp = g["importance"] or 0.0
            if not desc:
                desc = "(no description)"
            lines.append(f"  - [{domain},imp={imp:.0f}] {desc}")
        if not gaps:
            lines.append("  (none)")
    except Exception as e:
        lines.append(f"  (knowledge_gap read error: {e})")

    conn.close()
    return "\n".join(lines)


def _get_api_config() -> dict:
    """Read API configuration from openclaw.json."""
    try:
        cfg = json.loads(OPENCLAW_CONFIG.read_text())
    except Exception:
        cfg = {}

    result = {"base_url": None, "cf_token": None, "anthropic_key": None}

    def find_anthropic_key(obj, depth=0):
        if depth > 8 or not isinstance(obj, (dict, list)):
            return None
        items = obj.items() if isinstance(obj, dict) else enumerate(obj)
        for _, v in items:
            if isinstance(v, str) and v.startswith("sk-ant-"):
                return v
            found = find_anthropic_key(v, depth + 1)
            if found:
                return found
        return None

    result["anthropic_key"] = find_anthropic_key(cfg)

    providers = cfg.get("models", {}).get("providers", {})
    cf = providers.get("cloudflare-ai-gateway", {})
    if cf:
        result["base_url"] = cf.get("baseUrl", "").rstrip("/")
        cf_header = cf.get("headers", {}).get("cf-aig-authorization", "")
        if cf_header.startswith("Bearer "):
            result["cf_token"] = cf_header[7:]

    return result


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


def _route_output(agent_id: str, action_json: dict) -> dict:
    """
    Route System 2 output to appropriate script.
    Returns {routed: bool, type: str, detail: str}
    """
    action_type = action_json.get("type", "").lower()

    if action_type == "proposal":
        try:
            import post_proposal as pp
            title = str(action_json.get("title", "System 2 proposal"))[:200]
            content = str(action_json.get("content", ""))[:2000]
            evidence = action_json.get("evidence", [])
            if not isinstance(evidence, list):
                evidence = []

            result = pp.post_proposal(
                agent=agent_id,
                title=title,
                content=content,
                evidence=evidence,
            )
            return {
                "routed": not result.get("blocked", True),
                "type": "proposal",
                "detail": f"proposal_id={result.get('id','')} blocked={result.get('blocked')}",
            }
        except Exception as e:
            return {"routed": False, "type": "proposal", "detail": f"error: {e}"}

    elif action_type == "belief_update":
        try:
            import update_beliefs as ub
            output_json = {"belief_updates": [action_json.get("belief", {})]}
            ub.update_beliefs(agent_id, output_json)
            return {"routed": True, "type": "belief_update", "detail": "belief queued for review"}
        except Exception as e:
            return {"routed": False, "type": "belief_update", "detail": f"error: {e}"}

    elif action_type == "knowledge_gap":
        try:
            gap_id = f"kg-s2-{agent_id[:8]}-{uuid.uuid4().hex[:6]}"
            now = _now_iso()
            conn = _get_db()
            conn.execute(
                """INSERT OR IGNORE INTO knowledge_gaps
                   (id, agent_id, domain, description, importance, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    gap_id,
                    agent_id,
                    str(action_json.get("domain", ""))[:50],
                    str(action_json.get("description", ""))[:500],
                    float(action_json.get("importance", 5.0)),
                    now,
                ),
            )
            conn.commit()
            conn.close()
            return {"routed": True, "type": "knowledge_gap", "detail": f"gap_id={gap_id}"}
        except Exception as e:
            return {"routed": False, "type": "knowledge_gap", "detail": f"error: {e}"}

    return {"routed": False, "type": "unknown", "detail": f"unknown action type: {action_type}"}


def run_system2_think(agent_id: str, reason: str, mock_output: str | None = None) -> dict:
    """
    Main entry point for System 2 deliberate reasoning.

    Returns:
        {
          'success': bool,
          'reason': str,           # why we ran (from system1 escalation)
          'action_type': str,
          'routed': bool,
          'cap_hit': bool,
          'error': str | None,
        }
    """
    now = _now_iso()

    # ── STEP 1: Check daily cap FIRST ─────────────────────────────────────────
    # This is the FIRST thing we do — before any expensive computation.
    # Enforced in CODE, not just prompt.
    cap_hit, count_today = _check_daily_cap(agent_id)
    if cap_hit:
        _log_audit(
            agent_id,
            "system2_think",
            f"DAILY_CAP_REACHED count={count_today} reason='{reason[:100]}'",
        )
        print(f"[system2_think] DAILY_CAP_REACHED: {count_today}/{DAILY_CAP} today for {agent_id}")
        return {
            "success": False,
            "reason": reason,
            "action_type": None,
            "routed": False,
            "cap_hit": True,
            "error": "DAILY_CAP_REACHED",
        }

    # ── STEP 2: Build rich context (agent's OWN data only) ────────────────────
    context = _build_rich_context(agent_id)

    # ── STEP 3: Build deliberate reasoning prompt ─────────────────────────────
    prompt = f"""You are {agent_id}'s background cognitive process (System 2 — deliberate reasoning).

Escalation reason from System 1: {reason}

{context}

Task: Given the above context, what is the ONE most important thing to act on?

Generate exactly ONE action from these options:
1. A new proposal (if there's a gap the team should address)
2. A belief update (if something needs updating based on new evidence)
3. A knowledge gap (if something important is unknown)

Output ONLY valid JSON in one of these formats:

For proposal:
{{"type": "proposal", "title": "<max 100 chars>", "content": "<max 300 chars>", "evidence": []}}

For belief update:
{{"type": "belief_update", "belief": {{"content": "<max 200 chars>", "category": "fact", "confidence": 0.7, "importance": 5.0, "evidence_for": "<why>", "evidence_against": ""}}}}

For knowledge gap:
{{"type": "knowledge_gap", "domain": "<domain>", "description": "<max 200 chars>", "importance": 5.0}}

JSON only, no explanation:"""

    # ── STEP 4: Call AI (or use mock) ─────────────────────────────────────────
    raw_output = ""
    api_error = None

    if mock_output is not None:
        raw_output = mock_output
    else:
        api_cfg = _get_api_config()
        try:
            raw_output = _call_ai(prompt, api_cfg)
        except Exception as e:
            api_error = str(e)
            _log_audit(agent_id, "system2_think_error", f"API call failed: {api_error}")
            # Update cognitive_state back to idle on failure
            conn = _get_db()
            conn.execute(
                "UPDATE cognitive_state SET scan_status='idle', updated_at=? WHERE agent_id=?",
                (now, agent_id),
            )
            conn.commit()
            conn.close()
            return {
                "success": False,
                "reason": reason,
                "action_type": None,
                "routed": False,
                "cap_hit": False,
                "error": f"API_CALL_FAILED: {api_error}",
            }

    # ── STEP 5: Parse output ──────────────────────────────────────────────────
    action_json = {}
    parse_error = None
    try:
        # Strip any markdown code fences
        clean = raw_output.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        action_json = json.loads(clean)
    except Exception as e:
        parse_error = str(e)
        action_json = {
            "type": "knowledge_gap",
            "domain": "system2_parse_error",
            "description": f"System 2 output could not be parsed: {raw_output[:100]}",
            "importance": 3.0,
        }

    # ── STEP 6: Route to appropriate script ──────────────────────────────────
    route_result = _route_output(agent_id, action_json)

    # ── STEP 7: Increment system2_count_today, set system2_date=today ─────────
    today = _today_iso()
    conn = _get_db()
    conn.execute(
        """UPDATE cognitive_state
           SET system2_count_today = system2_count_today + 1,
               system2_date = ?,
               last_system2_run = ?,
               scan_status = 'idle',
               updated_at = ?
           WHERE agent_id = ?""",
        (today, now, now, agent_id),
    )
    conn.commit()
    conn.close()

    # ── STEP 8: Log to audit_log ──────────────────────────────────────────────
    _log_audit(
        agent_id,
        "system2_think",
        f"action_type={action_json.get('type','?')} routed={route_result['routed']} "
        f"reason='{reason[:100]}' route_detail={route_result['detail'][:100]}",
    )

    return {
        "success": True,
        "reason": reason,
        "action_type": action_json.get("type"),
        "routed": route_result["routed"],
        "cap_hit": False,
        "error": parse_error,
    }


def main():
    parser = argparse.ArgumentParser(description="System 2 deliberate reasoning")
    parser.add_argument("--agent", required=True, help="Agent ID")
    parser.add_argument("--reason", required=True, help="Escalation reason from System 1")
    parser.add_argument(
        "--mock-output",
        default=None,
        help='Mock JSON output from AI (for testing). E.g. \'{"type":"knowledge_gap",...}\'',
    )
    args = parser.parse_args()

    print(f"[system2_think] Starting deliberate reasoning for agent='{args.agent}'")
    print(f"[system2_think] Reason: {args.reason}")

    result = run_system2_think(args.agent, args.reason, mock_output=args.mock_output)

    if result.get("cap_hit"):
        print("[system2_think] ❌ DAILY_CAP_REACHED — not executed")
        sys.exit(1)
    elif result.get("error") == "DAILY_CAP_REACHED":
        print("[system2_think] ❌ DAILY_CAP_REACHED")
        sys.exit(1)
    elif not result.get("success"):
        print(f"[system2_think] ❌ Failed: {result.get('error')}")
        sys.exit(1)
    else:
        print(f"[system2_think] ✅ Generated: type={result['action_type']} routed={result['routed']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
