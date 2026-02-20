#!/usr/bin/env python3
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

DB_PATH = "/Users/acevashisth/.openclaw/workspace/state/vector.db"
WORKSPACE = Path("/Users/acevashisth/.openclaw/workspace")

Ticket = Tuple[str, str, str, str, str, str]

STATUS_MAP = {
    "✅ DONE": "DONE",
    "🔄 IN PROGRESS": "IN_PROGRESS",
    "🔜 NEXT": "TODO",
    "🔜 PENDING": "TODO",
    "🔴 BLOCKED": "BLOCKED",
    "⚠️ OPEN": "TODO",
    "⏳ WAITING": "BLOCKED",
    "DONE": "DONE",
    "IN_PROGRESS": "IN_PROGRESS",
    "TODO": "TODO",
    "BLOCKED": "BLOCKED",
}

PRIORITY_MAP = {
    "P0": "P0",
    "P1": "P1",
    "P2": "P2",
    "P3": "P3",
    "HIGH": "P1",
    "CRITICAL": "P0",
    "LOW": "P3",
    "MED": "P2",
}

COG_TICKETS: List[Ticket] = [
    ("COG-001", "Phase 0: Spawn Tracking + Budget Gate", "DONE", "P0", "FORGE", "Phases 0-4 all done. 5/5 PASS."),
    ("COG-002", "Phase 0-FIX: Provisional Bug in BEE TypeScript", "DONE", "P0", "FORGE", "db.ts + recall.ts fixed."),
    ("COG-003", "Phase 1: ACT-R Memory Table + Migration", "DONE", "P0", "FORGE", "8/8 PASS."),
    ("COG-004", "Phase 1B: PM Belief Persistence", "DONE", "P0", "FORGE", "8/8 PASS."),
    ("COG-005", "Phase 2: Beliefs Drive Action", "DONE", "P0", "FORGE", "8/8+8/8 PASS."),
    ("COG-006", "WIRE: spawn_pm.py + post_pm.py + PM BRAINs", "DONE", "P0", "FORGE", "6/6 adversarial attacks blocked."),
    ("COG-007", "Phase 3: Inter-Agent Messaging + Proposals Board", "DONE", "P0", "FORGE", "12/12 PASS."),
    ("COG-008", "Phase 4: Cognitive Loops System 1 + System 2", "DONE", "P0", "FORGE", "17/17 PASS (mock). Live API now verified."),
    ("COG-009", "Security Hardening I: Validator + Rate Limiting + DB Perms", "DONE", "P0", "FORGE", "48/48 PASS. 9/9 obfuscation blocked."),
    ("COG-010", "UI: Autoscroll Fix + Unread Indicator", "DONE", "P1", "FORGE", "commit 7efd2d3. tsc clean."),
    ("COG-FIX-001", "Clean Test Artifacts from Production DB", "DONE", "P0", "VECTOR", "56 test beliefs deleted."),
    ("COG-FIX-002", "BEE agentId to chief Namespace", "DONE", "P0", "VECTOR", "49 beliefs migrated. Config live."),
    ("COG-FIX-003", "knowledge_gaps Column Name Audit", "DONE", "P1", "VECTOR", "Clean. No gap_description refs."),
    ("COG-FIX-004", "Fix system1_scan + system2_think API Calls", "DONE", "P0", "FORGE", "openclaw agent subprocess. 17/17 pass. Live API verified."),
    ("COG-GAP-001", "ACT-R Time-Decay Not in Cognition Block Ordering", "TODO", "P3", "FORGE", "Accepted. Part of COG-011."),
    ("COG-GAP-002", "Manipulative action_implication Reaches Cognition Block", "TODO", "P2", "FORGE", "Accepted. Provisional gate mitigates."),
    ("COG-GAP-003", "PM Can Omit belief_updates JSON", "TODO", "P3", "FORGE", "Accepted. Silent skip."),
    ("COG-GAP-004", "Synonym Contradiction Not Detected Structurally", "TODO", "P3", "FORGE", "Accepted. reflect.py handles."),
    ("COG-GAP-005", "Provisionals Accumulate Without Auto-Trigger", "TODO", "P2", "VECTOR", "Accepted. By design."),
    ("COG-GAP-006", "System 1 Never Tested Live (Mock-Only)", "DONE", "P0", "VECTOR", "FIXED by COG-FIX-004. COG-TEST-005 PASS."),
    ("COG-GAP-007", "Cognitive Loop Crons Not Scheduled", "BLOCKED", "P1", "VECTOR", "Awaiting Chief approval for COG-OPS-001."),
    ("COG-GAP-008", "SQLite Row-Level Security", "TODO", "P3", "VECTOR", "Accepted. Structural limitation."),
    ("COG-GAP-009", "Tests Run Against Production DB", "TODO", "P1", "FORGE", "Open. Test isolation needed."),
    ("COG-GAP-010", "BEE Namespace Collision", "DONE", "P0", "VECTOR", "FIXED by COG-FIX-002."),
    ("COG-GAP-011", "__shared__ Empty Test Artifact", "DONE", "P1", "VECTOR", "FIXED. Artifact deleted."),
    ("COG-GAP-012", "No Real End-to-End Cognitive Cycle Run", "TODO", "P0", "FORGE", "Addressed by COG-TEST-001."),
    ("COG-GAP-013", "knowledge_gaps Column Name Mismatch", "DONE", "P2", "VECTOR", "FIXED by COG-FIX-003."),
    ("COG-GAP-014", "DB tickets vs md tickets Double-Tracking", "TODO", "P3", "VECTOR", "This sync script fixes it."),
    ("COG-GAP-015", "System 1/2 Direct API Calls Unviable", "DONE", "P0", "VECTOR", "FIXED by COG-FIX-004."),
    ("COG-011", "Phase 5A: Memory Scoping — Private/Shared/Global Routing", "IN_PROGRESS", "P0", "FORGE", "route_scope.py + pending_shared table + review_pending.py. FORGE running."),
    ("COG-012", "Phase 5B: BEE chief Namespace in PM Spawn Context", "TODO", "P0", "FORGE", "After COG-011."),
    ("COG-013", "Phase 5C: Haiku Query Expansion for Memory Retrieval", "TODO", "P1", "FORGE", "After COG-012."),
    ("COG-014", "Phase 5D: Work Induction — PMs Trigger Each Other", "TODO", "P1", "FORGE", "After COG-013."),
    ("COG-015", "Phase 6: Security Hardening II", "TODO", "P1", "SENTINEL", "After COG-014."),
    ("COG-016", "Phase 7: Embeddings + Semantic Re-Ranking", "TODO", "P2", "FORGE", "Stretch goal."),
    ("COG-TEST-001", "Cross-Phase Integration Test — Full A-Z Cycle", "TODO", "P0", "FORGE", "After COG-013."),
    ("COG-TEST-002", "Memory Routing Adversarial Tests", "TODO", "P0", "FORGE", "Part of COG-011."),
    ("COG-TEST-003", "BEE Extraction Tests", "TODO", "P0", "FORGE", "Part of COG-012."),
    ("COG-TEST-004", "Staggered Cron Adversarial Tests", "TODO", "P1", "SENTINEL", "After COG-OPS-001."),
    ("COG-TEST-005", "System 1 Live API Verification", "DONE", "P0", "VECTOR", "PASS. escalated=True on first real call."),
    ("COG-OPS-001", "Re-Enable Cognitive Loop Crons — Staggered per PM", "BLOCKED", "P1", "VECTOR", "REQUIRES CHIEF APPROVAL. Show schedule first."),
    ("COG-OPS-002", "Standup Integration — Proposals + Provisional Monitoring", "TODO", "P1", "VECTOR", "After COG-OPS-001."),
    ("COG-OPS-003", "Gateway Restart — 1464-Commit Build", "DONE", "P2", "VECTOR", "DONE. Chief restarted 2026-02-19 15:26 EST."),
]

BEE_TICKETS: List[Ticket] = [
    ("BEE-0", "BEE Audit + Architecture", "DONE", "P0", "FORGE", ""),
    ("BEE-1", "BEE ACT-R Recall Loop + Prefilter", "DONE", "P0", "FORGE", "Live. Injecting every turn."),
    ("BEE-1.5", "BEE Data Cleanup — Archive Garbage Beliefs", "DONE", "P1", "FORGE", "530 garbage archived."),
    ("BEE-2", "BEE Plugin Packaging + Schema", "DONE", "P0", "FORGE", "openclaw.plugin.json complete."),
    ("BEE-3", "BEE Profile Distillation", "DONE", "P1", "FORGE", ""),
    ("BEE-4", "BEE Open Source Launch", "IN_PROGRESS", "P1", "VECTOR", "Repo live. Content scheduled. Awaiting repo public."),
    ("BEE-SCHEMA", "Fix: Add Missing Schema Properties to openclaw.plugin.json", "DONE", "P0", "VECTOR", "agentId + 4 others added. Built. Deployed."),
    ("BEE-NS", "Fix: agentId to chief Namespace Separation", "DONE", "P0", "VECTOR", "49 beliefs migrated. Config live."),
    ("BEE-LAUNCH-1", "Make openclaw-bee Repo Public on vashkartik GitHub", "BLOCKED", "P1", "CHIEF", "Chief action required."),
    ("BEE-LAUNCH-2", "CF DNS for skyslabs.ai Custom Domain", "BLOCKED", "P2", "CHIEF", "Chief action required."),
    ("BEE-LAUNCH-3", "Replace INSERT_REPO_LINK Placeholders in Content", "TODO", "P1", "VECTOR", "After repo public."),
    ("BEE-QUALITY", "BEE Extraction Quality — Filter System Message Noise", "TODO", "P1", "FORGE", "System messages extracted as beliefs. Needs filter."),
]

IGIP_TICKETS: List[Ticket] = [
    ("IND-030", "IGIP Chat System", "DONE", "P0", "FORGE", "Live at earthly-dashboard.pages.dev."),
    ("IND-031", "Real-Time Gov Data API Research", "DONE", "P0", "ORACLE", "Delivered."),
    ("IND-040", "Full IPS Meeting Prep — 7 Documents", "DONE", "P0", "FORGE", "Concept note, pitch deck, one-pager ready. Meeting: Saturday 2026-02-22."),
    ("IND-033", "IGIP Chat to CF Pages Function", "DONE", "P1", "FORGE", "Needs RESEND_API_KEY in CF env (Chief)."),
    ("IND-032", "Integrate Real-Time Data Feeds", "TODO", "P1", "FORGE", "Post-meeting. After IPS validates direction."),
]

AVIMEE_TICKETS: List[Ticket] = [
    ("AVH-070", "Shopify UI Research", "DONE", "P1", "ORACLE", ""),
    ("AVH-071", "Avimee USA Shop UI Overhaul", "DONE", "P0", "FORGE", "Deployed: avimee-shop.pages.dev."),
    ("AVH-050", "FBA Shipping Labels", "BLOCKED", "P1", "CHIEF", "Waiting on physical labels ETA ~1 week."),
    ("AVH-052", "Amazon Ads API Integration", "BLOCKED", "P1", "CHIEF", "Scaffold built. Needs real API token from Chief."),
    ("AVH-053", "Google Ads API Integration", "BLOCKED", "P1", "CHIEF", "Scaffold built. Needs OAuth credentials from Chief."),
]

SKYSPHERE_TICKETS: List[Ticket] = [
    ("SKY-001", "Skysphere Website Deployed", "DONE", "P1", "FORGE", "skysphere-website.pages.dev"),
    ("SKY-002", "Content Pipeline — 90 Posts in SQLite", "DONE", "P1", "SAGE", "30 Twitter/day, 30 LinkedIn MWF, 30 LinkedIn company TTS."),
    ("SKY-003", "Custom Domain skyslabs.ai CF DNS", "BLOCKED", "P2", "CHIEF", "CF DNS permissions needed."),
]

GOLD_TICKETS: List[Ticket] = [
    ("GOLD-001", "Gold Trading Bot Core + Pipeline", "DONE", "P2", "FORGE", "Full build complete."),
    ("GOLD-009", "GEX Integration Research", "TODO", "P2", "FORGE", "Research done. Implementation pending."),
    ("GOLD-DEPLOY", "Gold Bot Live Deploy — MT5 Environment", "BLOCKED", "P2", "CHIEF", "Needs Windows/MT5 environment decision."),
]

ALL_PROJECTS: Dict[str, List[Ticket]] = {
    "cognition": COG_TICKETS,
    "bee": BEE_TICKETS,
    "igip": IGIP_TICKETS,
    "avimee-herbal": AVIMEE_TICKETS,
    "skysphere": SKYSPHERE_TICKETS,
    "gold-trading": GOLD_TICKETS,
}


def get_project_slug(ticket_id: str) -> str:
    if ticket_id.startswith("COG-"):
        return "cognition"
    if ticket_id.startswith("BEE-"):
        return "bee"
    if ticket_id.startswith("IND-"):
        return "igip"
    if ticket_id.startswith("AVH-"):
        return "avimee-herbal"
    if ticket_id.startswith("SKY-"):
        return "skysphere"
    if ticket_id.startswith("GOLD-"):
        return "gold-trading"
    return "general"


def normalize_status(status: str) -> str:
    return STATUS_MAP.get(status.strip(), status.strip())


def normalize_priority(priority: str) -> str:
    return PRIORITY_MAP.get(priority.strip().upper(), priority.strip().upper())


def sync_tickets(conn: sqlite3.Connection, ticket_list: List[Ticket]) -> Tuple[int, int]:
    now = datetime.now().isoformat()
    inserted = 0
    updated = 0

    for ticket_id, title, raw_status, raw_priority, assignee, notes in ticket_list:
        status = normalize_status(raw_status)
        priority = normalize_priority(raw_priority)
        project_slug = get_project_slug(ticket_id)

        existing = conn.execute(
            "SELECT created_at FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()

        created_at = existing[0] if existing and existing[0] else now
        completed_at = now if status == "DONE" else None

        conn.execute(
            """
            INSERT OR REPLACE INTO tickets (
                id, title, type, priority, status, assignee, project_slug,
                depends_on, directive, spec, result, checkpoint,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                title,
                "feature",
                priority,
                status,
                assignee,
                project_slug,
                None,
                None,
                None,
                None,
                notes,
                created_at,
                now,
                completed_at,
            ),
        )

        if existing:
            updated += 1
        else:
            inserted += 1

    return inserted, updated


def task_line(ticket: Ticket) -> str:
    ticket_id, title, status, *_ = ticket
    if status == "DONE":
        box = "x"
    elif status == "IN_PROGRESS":
        box = "~"
    else:
        box = " "
    return f"- [{box}] {ticket_id}: {title}"


def write_tasks_markdown(path: Path, heading: str, tickets: List[Ticket]) -> None:
    done = [t for t in tickets if t[2] == "DONE"]
    in_progress = [t for t in tickets if t[2] == "IN_PROGRESS"]
    blocked = [t for t in tickets if t[2] == "BLOCKED"]
    pending = [t for t in tickets if t[2] not in {"DONE", "IN_PROGRESS", "BLOCKED"}]

    content = [
        heading,
        "",
        "## COMPLETED",
        *[task_line(t) for t in done],
        "",
        "## IN PROGRESS",
        *([task_line(t) for t in in_progress] or ["- [ ] None"]),
        "",
        "## PENDING",
        *([task_line(t) for t in pending] or ["- [ ] None"]),
        "",
        "## BLOCKED",
        *([task_line(t) for t in blocked] or ["- [ ] None"]),
        "",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(content), encoding="utf-8")


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        per_project = {}
        total_inserted = 0
        total_updated = 0

        for slug, tickets in ALL_PROJECTS.items():
            inserted, updated = sync_tickets(conn, tickets)
            per_project[slug] = {"inserted": inserted, "updated": updated, "total": len(tickets)}
            total_inserted += inserted
            total_updated += updated

        conn.commit()

    finally:
        conn.close()

    write_tasks_markdown(
        WORKSPACE / "projects" / "cognition" / "TASKS.md",
        "# Cognition — Agent Cognitive Architecture",
        COG_TICKETS,
    )
    write_tasks_markdown(
        WORKSPACE / "projects" / "bee" / "TASKS.md",
        "# BEE — Belief Extraction Engine",
        BEE_TICKETS,
    )

    print("Ticket sync complete")
    print(f"Total inserted: {total_inserted}")
    print(f"Total updated: {total_updated}")
    print("By project:")
    for slug, stats in per_project.items():
        print(
            f"- {slug}: inserted={stats['inserted']}, updated={stats['updated']}, expected_total={stats['total']}"
        )


if __name__ == "__main__":
    main()
