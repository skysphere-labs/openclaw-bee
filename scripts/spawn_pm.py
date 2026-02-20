#!/usr/bin/env python3
"""
spawn_pm.py — VECTOR's pre-spawn cognitive injector.

Builds a <cognitive-context> block for VECTOR to prepend to PM spawn tasks.
This ensures every PM spawns with:
  1. Relevant memories from past tasks
  2. Their active beliefs (private + shared)
  3. The PM output format instruction (so they remember to output belief_updates)

Usage:
    python3 spawn_pm.py --agent forge --task "Implement JWT auth" --ticket FORGE-AUTH-001

Output (stdout): A formatted <cognitive-context> block ready to prepend to the spawn task.

Handles gracefully:
  - Empty memories DB → shows empty memories section
  - No active beliefs → shows "forming from scratch"
  - Missing PM_OUTPUT_FORMAT_INSTRUCTION.txt → shows inline fallback
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path("/Users/acevashisth/.openclaw/workspace/scripts")
DB_DEFAULT = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
FORMAT_INSTRUCTION_PATH = SCRIPTS / "PM_OUTPUT_FORMAT_INSTRUCTION.txt"


def _fallback_terms(task: str) -> list[str]:
    terms = [t.lower() for t in re.findall(r"[A-Za-z0-9_]{5,}", task or "")]
    out = []
    seen = set()
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    if not out and task and task.strip():
        out = task.lower().split()[:1]
    return out


def expand_keywords(agent: str, task: str) -> list[str]:
    """Expand task into retrieval keywords; never raises."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "expand_retrieval_query.py"),
             "--task", task,
             "--agent", agent],
            capture_output=True, text=True, timeout=90
        )
        if result.returncode != 0:
            return _fallback_terms(task)
        data = json.loads(result.stdout.strip() or "[]")
        if isinstance(data, list) and len(data) > 0:
            return [str(x).strip().lower() for x in data if str(x).strip()]
        return _fallback_terms(task)
    except Exception:
        return _fallback_terms(task)


def get_memories(agent: str, task: str, limit: int = 5) -> str:
    """Call retrieve_memories.py to get top memories for this agent + task query."""
    queries = expand_keywords(agent, task)
    try:
        cmd = [sys.executable, str(SCRIPTS / "retrieve_memories.py"),
               "--agent", agent,
               "--limit", str(limit)]
        if queries:
            cmd += ["--queries", *queries]
        else:
            cmd += ["--query", task]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        output = result.stdout.strip()
        if result.returncode != 0 or not output:
            # hard fallback to single-query path if multi-query yielded nothing/error
            fallback = subprocess.run(
                [sys.executable, str(SCRIPTS / "retrieve_memories.py"),
                 "--agent", agent,
                 "--query", task,
                 "--limit", str(limit)],
                capture_output=True, text=True, timeout=15
            )
            fb_out = fallback.stdout.strip()
            if fb_out:
                return fb_out
            return "(No memories found — this agent is starting fresh)"
        return output
    except subprocess.TimeoutExpired:
        return "(Memory retrieval timed out — skipping)"
    except Exception as e:
        return f"(Memory retrieval error: {e})"


def get_cognition_block(agent: str, db_path: Path) -> str:
    """Call build_pm_cognition_block.py to get the pm-cognition XML block."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "build_pm_cognition_block.py"),
             "--agent", agent,
             "--db", str(db_path)],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout.strip()
        if result.returncode != 0 or not output:
            # Graceful fallback — return minimal block
            return (
                "<pm-cognition>\n"
                "## Your beliefs (private)\n"
                "No prior beliefs for this agent — forming from scratch.\n"
                "\n## Shared context (from VECTOR)\n"
                "No shared context available.\n"
                "</pm-cognition>"
            )
        return output
    except subprocess.TimeoutExpired:
        return (
            "<pm-cognition>\n"
            "## Your beliefs (private)\n"
            "Belief retrieval timed out — starting fresh.\n"
            "\n## Shared context (from VECTOR)\n"
            "No shared context available.\n"
            "</pm-cognition>"
        )
    except Exception as e:
        return (
            f"<pm-cognition>\n"
            f"## Your beliefs (private)\n"
            f"Belief retrieval error ({e}) — starting fresh.\n"
            f"\n## Shared context (from VECTOR)\n"
            f"No shared context available.\n"
            f"</pm-cognition>"
        )


def get_format_instruction() -> str:
    """Read PM_OUTPUT_FORMAT_INSTRUCTION.txt. Returns inline fallback if missing."""
    if FORMAT_INSTRUCTION_PATH.exists():
        try:
            return FORMAT_INSTRUCTION_PATH.read_text().strip()
        except Exception as e:
            pass  # Fall through to inline fallback

    # Inline fallback — minimal but functional
    return (
        'At the END of your task response, output this JSON on a single line:\n'
        '{"belief_updates":[{"content":"...","category":"fact","confidence":0.8,'
        '"importance":5,"action_implication":"...","evidence_for":"...","evidence_against":"..."}],'
        '"memory_operations":[],"knowledge_gaps":[]}\n'
        'Empty arrays are fine. This is how you remember across tasks.'
    )


def build_cognitive_context(agent: str, task: str, ticket: str, db_path: Path) -> str:
    """Assemble the full <cognitive-context> block."""
    memories = get_memories(agent, task)
    cognition_block = get_cognition_block(agent, db_path)
    format_instruction = get_format_instruction()

    lines = [
        f'<cognitive-context agent="{agent}" ticket="{ticket}">',
        "",
        "<memories>",
        memories,
        "</memories>",
        "",
        cognition_block,
        "",
        "<output-format-reminder>",
        format_instruction,
        "</output-format-reminder>",
        "",
        "</cognitive-context>",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Build cognitive context injection for PM spawns."
    )
    parser.add_argument("--agent", required=True, help="PM agent ID (e.g. forge, ghost)")
    parser.add_argument("--task", required=True, help="Task description (used for memory query)")
    parser.add_argument("--ticket", default="UNKNOWN-000", help="Ticket ID (e.g. FORGE-001)")
    parser.add_argument("--db", default=str(DB_DEFAULT), help="Path to vector.db")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"WARNING: DB not found at {db_path} — memories and beliefs will be empty",
            file=sys.stderr
        )

    context = build_cognitive_context(args.agent, args.task, args.ticket, db_path)
    print(context)


if __name__ == "__main__":
    main()
