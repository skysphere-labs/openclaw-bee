# VECTOR — Cognitive Architecture v2

> An autonomous agent operating system built on OpenClaw, with a full cognitive architecture: persistent beliefs, ACT-R memory, inter-agent messaging, autonomous cognitive loops, and belief extraction from real conversations.

---

## Why This Matters for OpenClaw Users

OpenClaw gives you agents. Powerful ones. But out of the box, every agent spawn starts cold — no memory of what it did last week, no awareness of what other agents are doing right now, no accumulated beliefs about your preferences or your codebase. Every time you spawn a PM, it's a stranger walking in. It reads whatever context you inject, does its job, and vanishes. The next spawn knows nothing.

This architecture fixes that at the foundation. VECTOR v2 gives every PM agent a **persistent belief system** — structured, confidence-weighted beliefs that survive across spawns and accumulate over time. When FORGE works on your auth system for the third time, it already knows that JWT expiry has been a recurring problem, that the team prefers short-lived tokens, and that the last two fixes introduced session edge cases. It doesn't have to rediscover this. It carries it in.

Beyond persistence, VECTOR v2 adds **autonomous cognitive cycles**. Every 4 hours, all 5 PMs run a System 1 scan — a fast, lightweight check that surfaces stale proposals, belief contradictions, and unread inter-agent messages. When something needs deeper reasoning, a System 2 deep-think kicks in (rate-capped at 2/day per PM). The PMs are not passive tools waiting to be called. They are active reasoners that self-monitor and escalate when something is wrong. This runs entirely without Chief intervention, on a cron, continuously.

The third pillar is **belief extraction from real conversations**. VECTOR's BEE (Belief Extraction Engine) reads Chief's actual messages and extracts structured beliefs about preferences, priorities, and constraints. These beliefs live in a protected `chief` namespace. Every PM spawn gets the top 5 Chief beliefs injected as read-only context — "Chief's observed preferences." The PMs aren't guessing what matters. They're reading from a living, continuously updated model of the person they serve. This is the difference between a tool and a system that actually learns who you are.

---

## V1 → V2: What Changed

### V1 — Base OpenClaw Setup

A standard OpenClaw workspace with basic PM infrastructure: task files (`tasks/*.md`), memory files (`memory/pm-{name}/lessons.md`), `SOUL.md`, `USER.md`. No autonomous cognition. PMs were called, ran tasks, completed — but forgot everything. No beliefs, no memory persistence, no inter-PM communication, no autonomous cognitive cycles. Each spawn started from zero.

The V1 system worked — FORGE could write code, GHOST could sync repos, ORACLE could research AI news — but every spawn was stateless. FORGE didn't remember that the last TypeScript migration broke SSR. SENTINEL didn't carry forward the pattern that API latency spikes at 07:00 UTC. COMPASS didn't accumulate knowledge about what product directions Chief had already ruled out. Compute was being spent re-discovering things that had already been learned.

### V2 — Cognitive Architecture

Built in 5 phases + security hardening + end-to-end integration proof. Every phase was tested before the next one started. No phase shipped without passing tests.

---

#### Phase 0: Spawn Tracking + Budget Gates

Before adding cognition, we needed accountability. Every PM spawn now gets logged to SQLite with ticket ID and cost estimate. Hard budget gates refuse spawns if daily cost exceeds $25.

**Scripts:**
- `spawn_and_track.py` — logs every PM spawn to SQLite with ticket ID, cost estimate
- `complete_and_track.py` — marks completion, logs actual cost
- `check_budget.py` — hard gate: refuses spawn if daily cost > $25

**Why it matters:** You can't build a system that spawns agents autonomously without knowing what it costs. The budget gate is the first line of defense against runaway compute. Before we added cognition that would trigger more spawns, we had to make sure there was a hard ceiling.

**Test:** `test_phase0.py` — **5/5 PASS**

---

#### Phase 1: ACT-R Memory System

The first real cognition layer. Instead of flat markdown lesson files, memories now live in a structured SQLite table with ACT-R activation scoring — the same memory model used in cognitive science for decades. Each memory has an importance weight, a decay factor, and an access frequency count. Memories that are accessed more often decay more slowly. Old memories that are never touched fade out of retrieval range.

**Scripts:**
- `migrate_lessons_to_memories.py` — migrated all existing lessons from markdown to structured DB
- `retrieve_memories.py` — retrieves relevant memories using activation score ordering
- `promote_to_shared.py` — promotes PM memories to shared namespace

**Why it matters:** Flat lesson files don't scale. When FORGE has 200 lessons accumulated over months, returning all of them on every spawn drowns the context window and adds noise. ACT-R activation scoring means the most relevant, most recently accessed memories bubble to the top. The system gets smarter about what to surface as the corpus grows.

**Test:** `test_phase1.py` — **8/8 PASS**

---

#### Phase 1B: PM Belief Persistence

Memories are facts about the world. Beliefs are what PMs think — their working model of reality. Phase 1B adds a `beliefs` table with structured belief storage: content, confidence (0.0–1.0), category, importance, and status (`provisional` / `active` / `archived`).

**Scripts:**
- `update_beliefs.py` — write path for PM beliefs
- `pm_output_schemas.py` — validated JSON schema for PM task outputs (`BeliefUpdate`, `MemoryOperation`, `PMTaskOutput`)

**Why it matters:** A PM that completes a task and then forgets what it concluded is a PM that will make the same mistakes again. Phase 1B gives every PM a durable working model that persists between spawns. The validated JSON schema is critical: it enforces that PM outputs are machine-parseable, not free-text, so beliefs can be reliably extracted and stored.

**Test:** `test_phase1b.py` — **8/8 PASS**

---

#### Phase 2: Beliefs Drive Action

Phase 2 is where the system becomes self-aware. The cognition block is now assembled and injected into every PM spawn: private beliefs + shared context + Chief's observed preferences + ACT-R ranked memories. PMs no longer start cold — they start with their accumulated working model of the world.

**Scripts:**
- `build_pm_cognition_block.py` — assembles the full cognitive context block injected into every PM spawn
- `reflect.py` — daily belief reflection: promotes provisional→active (7-day stability threshold), archives contradictions
- `reflection_tracker.py` — tracks reflection history, uncertainty, contradictions, knowledge gaps

**Why it matters:** Without Phase 2, Phase 1 and 1B are inert. You have stored beliefs and memories, but nothing reads them. Phase 2 closes the loop: stored beliefs become injected context, which drives better PM decisions, which produce better beliefs. The reflection cycle means the belief store doesn't just accumulate — it self-organizes. Contradictions get flagged. Provisional beliefs that have been stable for 7 days get promoted to active. Stale beliefs get archived.

**Wiring — spawn + post PM:**
- `spawn_pm.py` — full PM spawn pipeline: build cognition block → expand retrieval query → retrieve memories → inject context → log spawn
- `post_pm.py` — full PM output pipeline: validate JSON → route scope → store beliefs → queue shared beliefs → store memories → log gaps
- `PM_OUTPUT_FORMAT_INSTRUCTION.txt` — injected into every PM task, specifies required JSON output format
- All 5 PM BRAIN.md files updated with cognitive architecture rules

**Test:** `test_phase2.py` — **8/8 + 8/8 PASS** | 6/6 adversarial attacks blocked

---

#### Phase 3: Inter-Agent Messaging + Proposals

PMs can now communicate with each other and with VECTOR through a validated messaging system. FORGE can flag a concern to SENTINEL. GHOST can post a proposal for VECTOR review. COMPASS can read ORACLE's research output directly.

**Scripts:**
- `read_agent_messages.py`, `send_agent_message.py` — read/write tools for direct PM-to-PM messages
- `post_proposal.py` — formal proposals that route through VECTOR review
- `validate_agent_message.py` — 3-tier validation: Tier 1 (28 format checks), Tier 2 (content sanitization), Tier 3 (threat flagging)

**Why it matters:** Isolated agents are limited agents. A PM that can't communicate can't escalate, can't collaborate, and can't surface compound insights that cross domain boundaries. Phase 3 enables the PM layer to function as a team, not as five independent tools. The 3-tier validation is not optional — inter-agent messaging is an injection surface, and every message is validated before it can influence any PM's context.

**Test:** `test_phase3.py` — **12/12 PASS**

---

#### Phase 4: Cognitive Loops (System 1 + System 2)

The autonomous heartbeat of the system. Every PM now runs a cognitive loop on a cron schedule, independent of Chief interaction.

- **System 1** (`system1_scan.py`) — fast autonomous scan (~1.5s): checks for stale proposals, belief contradictions, unread messages. Makes real LLM calls via `openclaw agent` subprocess. Escalates if needed.
- **System 2** (`system2_think.py`) — deep deliberate reasoning (capped at 2/day per PM): triggered on escalation. Produces structured action plan.
- **`cognitive_loop.py`** — orchestrates System 1 → escalation check → System 2 if needed → logs result to audit_log

**Why it matters:** This is the phase that makes VECTOR genuinely autonomous. Without cognitive loops, agents are reactive — they only think when called. With cognitive loops, agents are proactive — they check their own state, surface anomalies, and reason through them, all without Chief's involvement. The System 1 / System 2 split mirrors dual-process theory: fast pattern-matching for routine checks, slow deliberate reasoning reserved for genuine escalations.

**Test:** `test_phase4.py` — **17/17 PASS**

---

#### Security Hardening

Autonomous systems are attack surfaces. Phase 4 added cognitive loops that process messages; we needed to be sure those messages couldn't be weaponized.

- `validate_agent_message.py` — normalization layer blocks 9 obfuscation patterns (Unicode lookalikes, encoding tricks, injection attempts)
- Rate limiting: 5 messages/hour to VECTOR, 10/hour general
- DB permissions set to 600 (owner-only)
- `__shared__` namespace cleanup — removed stale entries that could pollute shared context

**Test:** `test_comprehensive.py` — **48/48 PASS** | `test_adversarial.py` — **7 PASS / 0 FAIL / 3 KNOWN GAPS**

---

#### Phase 5A: Memory Scoping

Not all beliefs should be visible to all PMs. Phase 5A adds a `scope` column to beliefs and memories: `private | shared | global`. A scoping rules engine determines where each belief goes. Shared and global beliefs require VECTOR approval before reaching the `__shared__` namespace.

**Scripts:**
- `route_scope.py` — deterministic rules engine: GLOBAL > SHARED > PRIVATE priority. Force rules for Chief instructions, deployment policies, cross-PM ground truth, PM-self-referential content.
- `pending_shared` table — staging queue: shared/global beliefs require VECTOR approval before reaching `__shared__`
- `review_pending.py` — VECTOR's approval/rejection tool for queued beliefs

**Additional fix:** ACT-R time-decay fix — today's belief ranks above a 30-day-old one of equal importance (previously the decay formula was underweighting recency).

**Why it matters:** Without scoping, every belief from every PM would compete in the same namespace. FORGE's private debugging notes would pollute ORACLE's research context. Phase 5A gives each PM a private belief space while enabling genuine cross-PM knowledge sharing through a controlled approval queue.

**Test:** `test_phase5a.py` — **12/12 PASS**

---

#### Phase 5B: BEE Chief Namespace in PM Context

Chief's beliefs — extracted from real conversations by the Belief Extraction Engine — now appear in every PM's cognition block as a read-only "Chief's observed preferences" section. The `chief` namespace is protected: no PM can write to it. Only BEE can write Chief beliefs.

**Scripts:**
- `build_pm_cognition_block.py` updated: adds top 5 beliefs from `agent_id='chief'`
- `post_pm.py`: `'chief'` added to `PROTECTED_AGENT_IDS`

**Why it matters:** Chief's preferences shouldn't have to be re-stated on every task. They should be part of every PM's background context automatically. Phase 5B means that if Chief has consistently expressed a preference for TypeScript over JavaScript, for lightweight Docker images, for concise commit messages — every PM knows this without being told. The protection ensures no PM can corrupt Chief's belief namespace through its own outputs.

**Test:** `test_phase5b.py` — **8/8 PASS**

---

#### Phase 5C: Haiku Query Expansion

Memory retrieval is only as good as the query. When FORGE is spawned to "fix JWT auth bug," a naive keyword search returns memories tagged "jwt" — but misses memories about "session management," "bearer tokens," or "token expiry" that are directly relevant. Phase 5C uses an LLM to expand task descriptions into 5–8 specific retrieval keywords before hitting the memories table.

**Scripts:**
- `expand_retrieval_query.py` — calls LLM via subprocess to expand a task description into 5–8 specific retrieval keywords
- `retrieve_memories.py` updated: `--queries kw1 kw2 kw3` flag for multi-keyword OR retrieval
- `spawn_pm.py` updated: expand task → retrieve with expanded keywords

**Example:** `"Fix JWT auth bug"` → `["jwt", "authentication", "token", "auth middleware", "session", "bearer token", "authorization header", "token expiry"]`

**Why it matters:** Semantic gap between task titles and memory tags is one of the biggest failure modes for retrieval-augmented systems. Query expansion bridges that gap with minimal overhead. Fallback is word extraction from the task title — the system degrades gracefully if the LLM call fails.

**Test:** `test_phase5c.py` — **8/8 PASS**

---

#### End-to-End Integration Proof

After 5 phases of incremental construction, we needed proof that the full pipeline worked as a complete cycle — not just in unit tests, but end-to-end.

**`test_cog_integration.py`** — full cycle proof:
1. `spawn_pm` called
2. Cognition block built (all 3 sections: private beliefs, shared context, Chief preferences)
3. `post_pm` called with PM output
4. Beliefs stored, shared beliefs queued in `pending_shared`, memories written
5. `system1_scan` runs — returns clean (no false positives on fresh state)

**Result: 8/8 PASS** — COG-GAP-012 closed. First real end-to-end cognitive cycle proven.

---

#### Automated Cognitive Crons

Two crons run the cognitive architecture continuously:

| Cron | ID | Schedule | What it does |
|------|----|----------|--------------|
| COG-LOOP | `c878c5ce` | Every 4h (08:00/12:00/16:00/20:00 EST) | `cognitive_loop.py` for all 5 PMs — System 1 scan, escalation check, System 2 if needed |
| COG-REFLECT | `e7512e43` | Daily 02:00 EST | `reflect.py` — belief promotion/archival across all PMs |

First COG-LOOP run: 20:00 EST Feb 19, 2026 — all 5 PMs (FORGE, GHOST, ORACLE, SENTINEL, COMPASS) checked in cleanly.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CHIEF (Human)                            │
│                                                             │
│  Conversations → BEE extracts beliefs → beliefs(chief)      │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                    VECTOR (Tier 0)                          │
│  Reads tickets → spawn_pm.py                                │
│                      │                                      │
│   expand_retrieval_query ──→ retrieve_memories              │
│   build_pm_cognition_block                                  │
│     ├── private beliefs (PM's own)                         │
│     ├── shared context (__shared__ namespace)               │
│     └── Chief's observed preferences (read-only)           │
│                      │                                      │
│                      ▼                                      │
│              [PM SPAWN with full context]                   │
└─────────────────────┬───────────────────────────────────────┘
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
      FORGE        GHOST        ORACLE
      SENTINEL     COMPASS
         │
         ▼
    PM produces structured JSON output
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    post_pm.py                               │
│                                                             │
│   validate JSON (pm_output_schemas.py)                      │
│         │                                                   │
│   route_scope.py (GLOBAL > SHARED > PRIVATE)               │
│         │                                                   │
│    ┌────┴────────────────┐                                  │
│    ▼                     ▼                                  │
│  beliefs(pm)         pending_shared                         │
│  memories            (awaits VECTOR approval)               │
│  knowledge_gaps      → review_pending.py                    │
│                          │                                  │
│                          ▼                                  │
│                    __shared__ namespace                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              COG-LOOP (every 4h, automated)                 │
│                                                             │
│   cognitive_loop.py for each PM:                           │
│                                                             │
│   system1_scan.py (~1.5s)                                  │
│     ├── stale proposals?                                    │
│     ├── belief contradictions?                              │
│     └── unread agent messages?                              │
│              │                                              │
│         escalate?                                           │
│              │                                              │
│         YES  ▼                                              │
│   system2_think.py (capped 2/day/PM)                       │
│     └── structured action plan → audit_log                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│          Inter-Agent Messaging (Phase 3)                    │
│                                                             │
│  PM → send_agent_message.py → validate_agent_message.py    │
│           (3-tier: format / sanitize / threat-flag)        │
│                      │                                      │
│              agent_messages table                           │
│                      │                                      │
│  PM → read_agent_messages.py (validated reads only)        │
│                                                             │
│  PM → post_proposal.py → proposals table → VECTOR review   │
└─────────────────────────────────────────────────────────────┘
```

---

## Test Evidence

| Test File | Result | Phase | What it covers |
|-----------|--------|-------|----------------|
| `test_phase0.py` | **5/5 PASS** | 0 | Spawn tracking, budget gates |
| `test_phase1.py` | **8/8 PASS** | 1 | ACT-R memory, activation scoring, retrieval |
| `test_phase1b.py` | **8/8 PASS** | 1B | Belief persistence, JSON schema validation |
| `test_phase2.py` | **8/8 + 8/8 PASS** | 2 | Cognition block assembly, belief reflection |
| `test_wiring.py` | **6/6 adversarial blocked** | 2 | Adversarial injection into spawn/post pipeline |
| `test_phase3.py` | **12/12 PASS** | 3 | Inter-agent messaging, proposals, validation |
| `test_phase4.py` | **17/17 PASS** | 4 | System 1 scan, System 2 think, cognitive loop |
| `test_comprehensive.py` | **48/48 PASS** | Security | Message validation, rate limiting, obfuscation |
| `test_adversarial.py` | **7P / 0F / 3KG** | Security | Adversarial attacks on validation layer |
| `test_phase5a.py` | **12/12 PASS** | 5A | Memory scoping, routing, pending_shared queue |
| `test_phase5b.py` | **8/8 PASS** | 5B | Chief namespace protection, read-only injection |
| `test_phase5c.py` | **8/8 PASS** | 5C | Query expansion, multi-keyword retrieval |
| `test_cog_integration.py` | **8/8 PASS** ← E2E | All | Full cognitive cycle: spawn → post → scan |

**Total: 120+ tests passing. Zero regressions across phases.**

---

## Scripts Reference

| Script | Purpose | Phase |
|--------|---------|-------|
| `spawn_and_track.py` | Log PM spawns to SQLite with ticket ID + cost estimate | 0 |
| `complete_and_track.py` | Mark spawn complete, log actual cost | 0 |
| `check_budget.py` | Hard gate: refuse spawn if daily cost > $25 | 0 |
| `migrate_lessons_to_memories.py` | One-time migration: markdown lessons → memories table | 1 |
| `retrieve_memories.py` | ACT-R-scored memory retrieval with multi-keyword OR support | 1 |
| `promote_to_shared.py` | Promote PM memories to shared namespace | 1 |
| `update_beliefs.py` | Write path for PM beliefs (content, confidence, category) | 1B |
| `pm_output_schemas.py` | Validated JSON schema: BeliefUpdate, MemoryOperation, PMTaskOutput | 1B |
| `build_pm_cognition_block.py` | Assemble full cognitive context block for PM spawn injection | 2 |
| `reflect.py` | Daily belief reflection: promote provisional→active, archive contradictions | 2 |
| `reflection_tracker.py` | Track reflection history, uncertainty, contradictions, knowledge gaps | 2 |
| `spawn_pm.py` | Full PM spawn pipeline: cognition block → query expand → retrieve → inject → log | 2 |
| `post_pm.py` | Full PM output pipeline: validate → route scope → store beliefs/memories → log gaps | 2 |
| `PM_OUTPUT_FORMAT_INSTRUCTION.txt` | Injected into every PM task — specifies required JSON output format | 2 |
| `read_agent_messages.py` | Read inter-agent messages (validated path only) | 3 |
| `send_agent_message.py` | Send PM-to-PM or PM-to-VECTOR messages | 3 |
| `post_proposal.py` | Submit formal proposal for VECTOR review | 3 |
| `validate_agent_message.py` | 3-tier validation: format (28 checks) / sanitize / threat-flag | 3 |
| `cognitive_state` (table) | Per-PM cognitive state: last scan, escalation count, scan status | 4 |
| `system1_scan.py` | Fast autonomous scan (~1.5s): proposals, contradictions, unread messages | 4 |
| `system2_think.py` | Deep deliberate reasoning (capped 2/day/PM): structured action plan | 4 |
| `cognitive_loop.py` | Orchestrate System 1 → escalate? → System 2 → audit_log | 4 |
| `route_scope.py` | Deterministic scoping rules engine: GLOBAL > SHARED > PRIVATE | 5A |
| `review_pending.py` | VECTOR approval/rejection tool for pending_shared queue | 5A |
| `expand_retrieval_query.py` | LLM-powered query expansion: task title → 5–8 retrieval keywords | 5C |
| `activation-scorer.py` | Standalone ACT-R activation scorer (utility) | 1 |
| `importance-seeder.py` | Seed importance scores for memory bootstrapping | 1 |
| `sync_tickets.py` | Sync tickets from markdown task files to SQLite | Ops |
| `health-check.sh` | System health verification script | Ops |
| `build-memory-index.sh` | Build FTS5 memory search index | Ops |

---

## DB Schema

All state lives in `state/vector.db` (SQLite). Key tables added in V2:

| Table | Purpose |
|-------|---------|
| `beliefs` | PM belief store: content, confidence, category, importance, status, scope, agent_id |
| `memories` | ACT-R memory store: content, importance, access_count, last_accessed, decay_factor, scope |
| `cognitive_state` | Per-PM cognitive state: last scan timestamp, escalation count, scan status |
| `agent_messages` | Inter-agent messages: sender, recipient, content, read status, timestamp |
| `proposals` | Formal proposals: sender, content, status (pending/approved/rejected), VECTOR notes |
| `pending_shared` | Staging queue: shared/global beliefs awaiting VECTOR approval |
| `knowledge_gaps` | Logged gaps from PM outputs: what PMs don't know and need to learn |
| `agent_sessions` | PM spawn log: ticket ID, agent, model, cost estimate, actual cost, duration |
| `audit_log` | Extended: all cognitive events, belief updates, message sends, scope decisions |

**Permissions:** `vector.db` is set to `600` (owner-only) — no group or world read.

---

## Cron Schedule

| Cron | ID | Schedule | Script | What it does |
|------|----|----------|--------|--------------|
| COG-LOOP | `c878c5ce` | 08:00 / 12:00 / 16:00 / 20:00 EST (daily) | `cognitive_loop.py` | Runs System 1 scan for all 5 PMs. Escalates to System 2 if needed. Logs to audit_log. |
| COG-REFLECT | `e7512e43` | 02:00 EST (daily) | `reflect.py` | Belief promotion/archival: provisional→active at 7-day threshold, archive contradictions. |

**First COG-LOOP run:** 20:00 EST Feb 19, 2026 — all 5 PMs checked in cleanly (FORGE, GHOST, ORACLE, SENTINEL, COMPASS).

These crons run independently of any user interaction. The system actively monitors itself around the clock.

---

## Getting Started

To wire this cognitive architecture into your own OpenClaw workspace:

### Prerequisites
- OpenClaw installed and configured
- Python 3.9+
- SQLite (comes with Python)
- A working `state/vector.db` (or create a fresh one)

### 1. Copy the scripts directory
```bash
cp -r scripts/ /path/to/your/workspace/scripts/
```

### 2. Run the migration scripts
```bash
# Initialize belief and memory tables
python scripts/migrate_lessons_to_memories.py

# Seed importance scores for existing memories
python scripts/importance-seeder.py

# Build the FTS5 memory search index
bash scripts/build-memory-index.sh
```

### 3. Update your PM BRAIN.md files
Add the cognitive architecture rules to each PM's `BRAIN.md`. At minimum, inject `PM_OUTPUT_FORMAT_INSTRUCTION.txt` into every PM task so outputs are machine-parseable.

```
[Your task here]

---
[contents of PM_OUTPUT_FORMAT_INSTRUCTION.txt]
```

### 4. Set up the cron jobs
```bash
# Cognitive loop — every 4h
# Add to your crontab or OpenClaw cron system:
# 0 8,12,16,20 * * * python /path/to/workspace/scripts/cognitive_loop.py --all-pms

# Daily belief reflection — 02:00
# 0 2 * * * python /path/to/workspace/scripts/reflect.py
```

### 5. Wire spawn and post hooks
Replace your PM spawn calls with `spawn_pm.py` and add `post_pm.py` after each PM completes:

```bash
# Before spawning a PM:
python scripts/spawn_pm.py --pm FORGE --ticket FORGE-001 --task "Your task description"

# After PM completes and returns JSON output:
python scripts/post_pm.py --pm FORGE --ticket FORGE-001 --output pm_output.json
```

### 6. Verify the integration
```bash
python scripts/test_cog_integration.py
# Expected: 8/8 PASS
```

If all 8 tests pass, the full cognitive cycle is working: spawn → cognition block → post → beliefs stored → scan clean.

---

## The Numbers

- **120+ tests** passing across 12 test suites
- **0 regressions** — each phase tested before the next began
- **48/48** comprehensive security tests passing
- **5 PM agents** (FORGE, GHOST, ORACLE, SENTINEL, COMPASS) running cognitive loops
- **2 automated crons** running 24/7
- **9 new DB tables** added in V2
- **30 scripts** in the cognitive architecture
- **First autonomous cognitive cycle** proven E2E: Feb 19, 2026

---

*VECTOR v2 — Built for Chief. Running on OpenClaw.*
