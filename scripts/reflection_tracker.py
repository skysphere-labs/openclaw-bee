#!/usr/bin/env python3
"""
reflection_tracker.py — Track task completions, trigger reflect.py when threshold met.
VECTOR calls this after every PM task completion (alongside complete_and_track.py).

Usage:
    python3 reflection_tracker.py --agent forge
    # Returns exit code 0 = no reflection needed, 1 = reflection triggered
"""
import argparse, sqlite3, subprocess, sys
from pathlib import Path

DB = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
SCRIPTS = Path("/Users/acevashisth/.openclaw/workspace/scripts")
REFLECTION_TASK_THRESHOLD = 5   # reflect after every 5 tasks
PROVISIONAL_THRESHOLD = 10      # or if 10+ provisionals pending

parser = argparse.ArgumentParser()
parser.add_argument("--agent", required=True)
args = parser.parse_args()

conn = sqlite3.connect(DB)

# Count completions since last reflection
completions = conn.execute("""
    SELECT COUNT(*) FROM audit_log
    WHERE agent=? AND action='complete'
    AND ts > COALESCE(
        (SELECT MAX(ts) FROM audit_log WHERE agent=? AND action='reflection'),
        '2000-01-01'
    )
""", (args.agent, args.agent)).fetchone()[0]

provisionals = conn.execute(
    "SELECT COUNT(*) FROM beliefs WHERE agent_id=? AND status='provisional'",
    (args.agent,)
).fetchone()[0]

conn.close()

should_reflect = completions >= REFLECTION_TASK_THRESHOLD or provisionals >= PROVISIONAL_THRESHOLD

if should_reflect:
    print(f"reflection_tracker: triggering reflection for {args.agent} (completions={completions}, provisionals={provisionals})")
    result = subprocess.run([sys.executable, str(SCRIPTS / "reflect.py"), "--agent", args.agent])
    sys.exit(1 if result.returncode else 0)
else:
    print(f"reflection_tracker: no reflection needed for {args.agent} (completions={completions}/{REFLECTION_TASK_THRESHOLD}, provisionals={provisionals}/{PROVISIONAL_THRESHOLD})")
    sys.exit(0)
