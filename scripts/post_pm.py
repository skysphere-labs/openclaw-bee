#!/usr/bin/env python3
"""
post_pm.py — VECTOR's post-task belief parser.
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path("/Users/acevashisth/.openclaw/workspace/scripts")
sys.path.insert(0, str(SCRIPTS))
from pm_output_schemas import validate_pm_output
from route_scope import route_scope

DB_DEFAULT = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
PROTECTED_AGENT_IDS = {"vector", "__shared__", "chief"}
VALID_CATEGORIES = {"identity", "goal", "preference", "decision", "fact"}
VALID_SCOPES = {"private", "shared", "global"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_output(agent_id: str, output_json: dict, db_path: Path) -> dict:
    errors = []

    if agent_id in PROTECTED_AGENT_IDS:
        return {
            "stored": 0,
            "pending": 0,
            "errors": [f"BLOCKED: agent_id '{agent_id}' is protected — PMs cannot write to this namespace"],
            "gaps": 0,
        }

    _, schema_errors = validate_pm_output(output_json)
    if schema_errors:
        errors.extend(schema_errors)

    conn = sqlite3.connect(str(db_path))
    stored = 0
    pending = 0
    gaps_stored = 0

    belief_updates = output_json.get("belief_updates", [])
    if not isinstance(belief_updates, list):
        errors.append("belief_updates is not a list — skipping")
        belief_updates = []

    for i, b in enumerate(belief_updates):
        if not isinstance(b, dict):
            errors.append(f"belief_updates[{i}]: not a dict — skipped")
            continue

        content = str(b.get("content", "")).strip()
        if len(content) < 10:
            errors.append(f"belief_updates[{i}]: content too short ({len(content)} chars, min 10) — rejected")
            continue
        if len(content) > 500:
            errors.append(f"belief_updates[{i}]: content too long ({len(content)} chars, max 500) — rejected")
            continue

        category = b.get("category", "fact")
        if category not in VALID_CATEGORIES:
            errors.append(f"belief_updates[{i}]: invalid category '{category}' — defaulting to 'fact'")
            category = "fact"

        confidence = float(b.get("confidence", 0.65))
        confidence = max(0.5, min(1.0, confidence))

        importance = float(b.get("importance", 5.0))
        importance = max(1.0, min(10.0, importance))

        declared_scope = str(b.get("scope", "private")).lower().strip()
        if declared_scope not in VALID_SCOPES:
            declared_scope = "private"

        routing = route_scope(content, agent_id, declared_scope)
        resolved_scope = routing.get("resolved_scope", "private")
        rule_matched = routing.get("rule_matched", "default:declared_scope")

        action_impl = str(b.get("action_implication", ""))[:500]
        evidence_for = str(b.get("evidence_for", ""))[:500]
        evidence_against = str(b.get("evidence_against", ""))[:500]
        scope_reason = str(b.get("scope_reason", ""))[:500]

        bid = f"pm-{agent_id[:8]}-{uuid.uuid4().hex[:8]}"

        try:
            if resolved_scope == "private":
                conn.execute(
                    """INSERT OR IGNORE INTO beliefs
                       (id, content, confidence, category, status, agent_id, source, importance,
                        action_implication, evidence_for, evidence_against, created_at, updated_at, scope)
                       VALUES (?,?,?,?,'provisional',?,?,?,?,?,?,?,?,?)""",
                    (
                        bid,
                        content,
                        confidence,
                        category,
                        agent_id,
                        f"pm_task:{agent_id}",
                        importance,
                        action_impl,
                        evidence_for,
                        evidence_against,
                        now_iso(),
                        now_iso(),
                        "private",
                    ),
                )
                stored += 1
            elif resolved_scope in ("shared", "global"):
                conn.execute(
                    """INSERT OR IGNORE INTO pending_shared
                       (id, source_agent, content_type, source_id, scope, content,
                        pm_justification, routing_rule_override, status, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"pend-{uuid.uuid4().hex[:12]}",
                        agent_id,
                        "belief",
                        bid,
                        resolved_scope,
                        content,
                        scope_reason,
                        rule_matched,
                        "pending",
                        now_iso(),
                    ),
                )
                pending += 1
            else:
                errors.append(f"belief_updates[{i}]: invalid resolved_scope '{resolved_scope}'")
        except Exception as e:
            errors.append(f"belief_updates[{i}]: DB insert failed — {e}")

    memory_ops = output_json.get("memory_operations", [])
    if isinstance(memory_ops, list):
        for op in memory_ops:
            if not isinstance(op, dict):
                continue
            operation = op.get("op", "store")
            content = str(op.get("content", "")).strip()
            importance = float(op.get("importance", 5.0))
            importance = max(1.0, min(10.0, importance))

            if operation == "store" and content:
                mid = uuid.uuid4().hex[:8]
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO memories
                           (id, agent_id, content, importance, source, created_at, scope)
                           VALUES (?,?,?,?,?,?,?)""",
                        (mid, agent_id, content, importance, f"pm_memory_op:{agent_id}", now_iso(), "private"),
                    )
                except Exception as e:
                    errors.append(f"memory_operations store failed: {e}")
            elif operation == "archive" and content:
                try:
                    conn.execute(
                        "UPDATE beliefs SET status='archived' WHERE agent_id=? AND content=?",
                        (agent_id, content),
                    )
                except Exception as e:
                    errors.append(f"memory_operations archive failed: {e}")

    knowledge_gaps = output_json.get("knowledge_gaps", [])
    if isinstance(knowledge_gaps, list):
        for gap in knowledge_gaps:
            if not isinstance(gap, dict):
                continue
            domain = str(gap.get("domain", "unknown"))[:100]
            description = str(gap.get("description", "")).strip()
            if not description or len(description) < 10:
                continue
            importance = float(gap.get("importance", 5.0))
            importance = max(1.0, min(10.0, importance))
            gid = uuid.uuid4().hex[:8]
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge_gaps
                       (id, agent_id, domain, description, importance)
                       VALUES (?,?,?,?,?)""",
                    (gid, agent_id, domain, description, importance),
                )
                gaps_stored += 1
            except Exception as e:
                errors.append(f"knowledge_gaps store failed: {e}")

    try:
        conn.execute(
            "INSERT INTO audit_log (agent, action, detail) VALUES (?,?,?)",
            (
                agent_id,
                "post_pm_update",
                json.dumps({"stored": stored, "pending": pending, "errors": len(errors), "gaps": gaps_stored}),
            ),
        )
    except Exception:
        pass

    conn.commit()
    conn.close()

    return {"stored": stored, "pending": pending, "errors": errors, "gaps": gaps_stored}


def main():
    parser = argparse.ArgumentParser(description="Parse and persist PM belief_updates after task completion.")
    parser.add_argument("--agent", required=True, help="PM agent ID (e.g. forge)")
    parser.add_argument("--output", default="", help="JSON string of PM output")
    parser.add_argument("--file", default="", help="Path to JSON file with PM output")
    parser.add_argument("--db", default=str(DB_DEFAULT), help="Path to vector.db")
    args = parser.parse_args()

    if args.file:
        try:
            raw = json.loads(Path(args.file).read_text())
        except json.JSONDecodeError as e:
            print(json.dumps({"stored": 0, "pending": 0, "errors": [f"Malformed JSON in file: {e}"], "gaps": 0}))
            sys.exit(0)
    elif args.output:
        try:
            raw = json.loads(args.output)
        except json.JSONDecodeError as e:
            print(json.dumps({"stored": 0, "pending": 0, "errors": [f"Malformed JSON: {e}"], "gaps": 0}))
            sys.exit(0)
    else:
        print(json.dumps({"stored": 0, "pending": 0, "errors": ["--output or --file required"], "gaps": 0}))
        sys.exit(1)

    db_path = Path(args.db)
    if not db_path.exists():
        print(json.dumps({"stored": 0, "pending": 0, "errors": [f"DB not found: {db_path}"], "gaps": 0}))
        sys.exit(1)

    print(json.dumps(process_output(args.agent, raw, db_path)))


if __name__ == "__main__":
    main()
