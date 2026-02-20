#!/usr/bin/env python3
"""Migrate pm_lessons to memories table. Non-destructive — original table untouched."""
import sqlite3, uuid
from pathlib import Path

DB = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")
conn = sqlite3.connect(DB)

lessons = conn.execute(
    "SELECT id, pm_name, lesson_text, created_at FROM pm_lessons"
).fetchall()
print(f"Found {len(lessons)} lessons to migrate")

migrated = 0
for lid, pm_name, lesson_text, created_at in lessons:
    existing = conn.execute(
        "SELECT id FROM memories WHERE source=?", (f"pm_lessons:{lid}",)
    ).fetchone()
    if existing:
        continue  # idempotent
    mem_id = str(uuid.uuid4())[:8]
    conn.execute(
        """INSERT INTO memories (id, agent_id, content, importance, source, created_at)
           VALUES (?,?,?,?,?,?)""",
        (mem_id, pm_name or "vector", lesson_text, 5.0, f"pm_lessons:{lid}", created_at or "now"),
    )
    migrated += 1

conn.commit()
conn.close()
print(f"Migrated {migrated} lessons (skipped {len(lessons)-migrated} already done)")
