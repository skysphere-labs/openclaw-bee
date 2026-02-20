# openclaw-bee · BEE (Belief Extraction Engine)

> Your AI agents forget everything between sessions. Every spawn starts cold.  
> All the context from last week — gone. The lessons learned, the edge cases found,  
> the architectural decisions made — erased. BEE fixes this at the foundation.

---

## The Problem Isn't Memory. It's Structure.

Most "memory" solutions for AI agents are retrieval wrappers. They store text, search text, inject text. That's not cognition — that's grep with extra steps.

The difference between an agent that *ran* last week and one that *learned* last week is structure. Not just what was stored, but **what kind of thing it is**, **who it belongs to**, **who's allowed to read it**, and **whether it's been validated**.

BEE is a full cognitive persistence layer for multi-agent systems built on [OpenClaw](https://github.com/skysphere-labs/openclaw). It gives your agents structured belief persistence, inter-agent memory scoping, adversarial namespace protection, and a self-monitoring cognitive loop — all wired into the agent spawn/close lifecycle automatically.

If you're spawning agents without this, you're leaving them blind.

---

## The 3-State Belief Architecture

Beliefs in BEE exist in one of three scopes. This is the design decision everything else flows from.

### Private
A PM's own working knowledge. No other agent reads it. FORGE's auth debugging notes, ORACLE's research hunches, SENTINEL's operational observations — all isolated. An agent's private beliefs are its own cognitive state. Nobody touches them.

### Shared
Beliefs that *might* matter to other agents — but need explicit approval before they propagate. Not everything one PM learns should become everyone's truth. Shared beliefs queue for VECTOR review. Only after approval do they cross namespace boundaries.

### Global
Architectural ground truth. Injected into every agent spawn automatically. These are the facts every agent must agree on: the stack, the auth model, the deployment topology. Only VECTOR writes here.

**Why does this matter?**

Because if any agent can write to any namespace, you get belief poisoning.

---

## Belief Poisoning — The Attack That Shaped This Design

This is the threat that most multi-agent frameworks ignore entirely.

If Agent A can write to Agent B's belief namespace, Agent A can inject false beliefs into Agent B's context. Not as a message — as *memory*. As something Agent B wakes up believing.

Consider: `"The auth system uses symmetric keys"` — written by a compromised or misbehaving agent into the global namespace. Agent B spawns next week. That belief is in its context. It makes architectural decisions based on poisoned ground truth. The bug compounds across every downstream task.

**BEE's 3-state system prevents this at the architecture level:**

- No agent can write to another agent's private namespace. Full stop.
- Shared beliefs queue for VECTOR review before propagating — they don't auto-inject.
- Global beliefs come from VECTOR only. Authority escalation attempts (an agent claiming VECTOR identity to get global write access) are caught and blocked by the message validator.

The validator runs 28 format checks, content sanitization, threat flagging, and rate limiting on every inter-agent message. It's not optional. It's the only way inter-agent communication doesn't become a security surface.

---

## Cognitive Loop — Agents That Self-Monitor

Agents don't just hold beliefs. They check themselves.

**System 1** (fast scan, ~1.5s) runs every 4 hours: checks for contradictions, stale proposals, and unread messages. If the scan is clean, the agent logs idle. If something needs attention, it escalates.

**System 2** (deep think) triggers on escalation only. Deliberate, structured reasoning. Rate-capped at 2 runs per agent per day — because expensive reasoning should be earned, not reflexive.

The cognitive loop runs on cron: 08:00, 12:00, 16:00, and 20:00. Between active tasks, agents are still thinking — checking their own belief state, flagging contradictions, monitoring for gaps. The system doesn't need you to drive it.

Beliefs also go through nightly reflection (02:00): provisional beliefs that have held for 7 days without contradiction are promoted to active. Contradicted beliefs are archived. The belief corpus self-corrects.

---

## Memory That Gets Smarter Over Time

Flat lesson files don't scale. Two hundred lessons in a PM's history means two hundred lessons injected into every spawn — context window drowned before the task starts.

BEE uses ACT-R activation scoring (Adaptive Control of Thought-Rational — the cognitive science model from Carnegie Mellon) to rank memories. Each memory carries:

- **Importance weight** — how significant was this when learned?
- **Decay factor** — how fast does it fade without use?
- **Access frequency** — how often has it been retrieved?

```
activation = importance × (1 - decay^days) × log(access_count + 1)
```

A memory written yesterday about a critical security decision outranks a 6-month-old preference nobody's touched since. The corpus gets smarter as it grows. Retrieval also uses LLM-powered query expansion (via Haiku) — searching for "JWT auth bug" surfaces "token expiry," "bearer token validation," "session edge cases." Nothing gets missed because of literal-string mismatch.

---

## Critical Tests

157 tests total. Here are the ones that matter:

| Test | What it proves |
|------|---------------|
| **Belief poisoning injection** | Adversarial agent attempts to write to another agent's private namespace → blocked |
| **Authority escalation** | Agent claims VECTOR identity to get global write access → blocked by validator |
| **Belief contradiction detection** | Two agents write contradictory beliefs about the same fact → flagged, held for VECTOR review |
| **Integration: full cognitive loop** | Spawn → cognition block built → beliefs stored → System 1 scan clean → 8/8 |
| **6 injection attacks on PM output pipeline** | Malicious content attempts to smuggle through PM output format → all 6 blocked |

The 3 known gaps on the adversarial suite are documented in the repo. Gaps you pretend don't exist are the ones that cause incidents. We show the full surface.

```
test_phase0.py         5/5    PASS
test_phase1.py         8/8    PASS
test_phase1b.py        8/8    PASS
test_phase2.py         16/16  PASS
test_phase3.py         12/12  PASS
test_phase4.py         17/17  PASS
test_comprehensive.py  48/48  PASS
test_adversarial.py    7P / 0F / 3KG (known gaps documented)
test_phase5a.py        12/12  PASS
test_phase5b.py        8/8    PASS
test_phase5c.py        8/8    PASS
test_cog_integration   8/8    PASS
```

---

## Install

**Via ClawHub** (recommended):
```bash
npx clawhub install openclaw-bee
```

**Via npm:**
```bash
npm install openclaw-bee
```

**Via GitHub:**
```bash
npm install github:skysphere-labs/openclaw-bee
```

Then add to `~/.openclaw/openclaw.json`:

```json
{
  "extensions": {
    "entries": {
      "bee": {
        "enabled": true,
        "config": {
          "dbPath": "~/.openclaw/workspace/state/vector.db",
          "agentId": "main",
          "extractionEnabled": true,
          "extractionModel": "anthropic/claude-haiku-4-5"
        }
      }
    }
  }
}
```

Restart the gateway:
```bash
openclaw gateway restart
```

BEE runs its schema migration on first start and begins capturing beliefs immediately.

---

**[clawhub.com/skills/openclaw-bee](https://clawhub.com/skills/openclaw-bee)** · **[npmjs.com/package/openclaw-bee](https://npmjs.com/package/openclaw-bee)** · **[github.com/skysphere-labs/openclaw-bee](https://github.com/skysphere-labs/openclaw-bee)** · MIT License

---

## Philosophy

Build the ceiling first. Then build the cognition.

An autonomous system that spawns agents without spend tracking is a system you can't safely leave unsupervised. BEE ships with hard gates: daily spend > $25 refuses new spawns. Not as safety theater — as the literal guardrail that makes everything else safe to run unattended.

The belief architecture came from the same principle: you can't build multi-agent systems where any agent can write anything to any shared state. That's not a system — that's a race condition with LLMs involved. The 3-state scoping, the VECTOR-only write authority, the validator — these aren't features bolted on for security theater. They're the architecture. Everything else depends on them.

If you're building serious multi-agent infrastructure, your agents should be accumulating knowledge, not resetting every spawn.

---

*Built by [Skysphere AI Labs](https://skyslabs.ai).*
