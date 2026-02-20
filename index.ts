/**
 * BEE — Belief Extraction Engine (OpenClaw Plugin) v3
 *
 * Phase 1: Agent Memory + Semantic Recall
 *   - memories table with ACT-R activation scoring
 *   - embedding BLOB on beliefs + async OpenAI text-embedding-3-small generation
 *   - Semantic recall (cosine similarity) with keyword fallback
 *   - Belief deduplication at insert (cosine > 0.92 → merge)
 *   - Importance scoring (1-10) at extraction
 *   - memories injected into PM (subagent) spawns
 *
 * Recall: hooks `before_agent_start` to inject relevant beliefs as prependContext.
 * Extraction: hooks `agent_end` to extract durable beliefs via Haiku LLM.
 * Migration: hooks `gateway_start` to safely run schema migrations.
 * Tracking: hooks `session_start` / `session_end` to audit subagent spawns.
 */

import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

// ── Types ────────────────────────────────────────────────────────────

interface BeeConfig {
  dbPath: string;
  maxCoreBeliefs: number;
  maxActiveBeliefs: number;
  maxRecalledBeliefs: number;
  maxOutputChars: number;
  debug: boolean;
  extractionEnabled: boolean;
  extractionModel: string;
  extractionMinConfidence: number;
  agentId: string;
  spawnBudgetWarning: number;
}

interface BeliefRow {
  id: string;
  content: string;
  confidence: number;
  category: string;
  status: string;
  decay_sensitivity: string | null;
  activation_score: number;
  last_relevant: string | null;
  updated_at: string | null;
  created_at: string | null;
}

type RecallTier = "core" | "active" | "recalled";

interface TieredBelief extends BeliefRow {
  tier: RecallTier;
}

interface ExtractedBelief {
  content: string;
  category: string;
  confidence: number;
  importance: number;
  reasoning: string;
}

// ── Config ───────────────────────────────────────────────────────────

function parseConfig(raw: Record<string, unknown>): BeeConfig {
  const defaultDb = join(homedir(), ".openclaw", "workspace", "state", "vector.db");
  return {
    dbPath: typeof raw.dbPath === "string" ? raw.dbPath : defaultDb,
    maxCoreBeliefs: typeof raw.maxCoreBeliefs === "number" ? raw.maxCoreBeliefs : 10,
    maxActiveBeliefs: typeof raw.maxActiveBeliefs === "number" ? raw.maxActiveBeliefs : 5,
    maxRecalledBeliefs: typeof raw.maxRecalledBeliefs === "number" ? raw.maxRecalledBeliefs : 5,
    maxOutputChars: typeof raw.maxOutputChars === "number" ? raw.maxOutputChars : 2000,
    debug: raw.debug === true,
    extractionEnabled: raw.extractionEnabled !== false,
    extractionModel:
      typeof raw.extractionModel === "string" ? raw.extractionModel : "anthropic/claude-haiku-4-5",
    extractionMinConfidence:
      typeof raw.extractionMinConfidence === "number" ? raw.extractionMinConfidence : 0.55,
    agentId: typeof raw.agentId === "string" ? raw.agentId : "vector",
    spawnBudgetWarning: typeof raw.spawnBudgetWarning === "number" ? raw.spawnBudgetWarning : 20,
  };
}

// ── OpenAI key ────────────────────────────────────────────────────────

function readOpenAIKey(): string {
  try {
    const cfgPath = join(homedir(), ".openclaw", "openclaw.json");
    const d = JSON.parse(readFileSync(cfgPath, "utf-8")) as Record<string, unknown>;
    // Primary: agents.defaults.memorySearch.remote.apiKey
    const remote = (
      (d.agents as Record<string, unknown> | undefined)?.defaults as
        | Record<string, unknown>
        | undefined
    )?.memorySearch as Record<string, unknown> | undefined;
    const primary = (remote?.remote as Record<string, unknown> | undefined)?.apiKey;
    if (typeof primary === "string" && primary.startsWith("sk-")) return primary;
    // Fallback: first providers entry with sk- key
    const providers = d.providers as Record<string, Record<string, unknown>> | undefined;
    if (providers) {
      for (const val of Object.values(providers)) {
        const k = val?.apiKey;
        if (typeof k === "string" && k.startsWith("sk-")) return k;
      }
    }
  } catch {
    /* ignore */
  }
  return "";
}

// ── Embedding utilities ───────────────────────────────────────────────

async function generateEmbedding(text: string, apiKey: string): Promise<Float32Array | null> {
  if (!apiKey) return null;
  try {
    const res = await fetch("https://api.openai.com/v1/embeddings", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "text-embedding-3-small", input: text.slice(0, 8000) }),
    });
    const data = (await res.json()) as { data?: Array<{ embedding: number[] }> };
    const vec = data.data?.[0]?.embedding;
    if (!vec) return null;
    return new Float32Array(vec);
  } catch {
    return null;
  }
}

function cosineSimilarity(a: Float32Array, b: Float32Array): number {
  let dot = 0,
    magA = 0,
    magB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    magA += a[i] * a[i];
    magB += b[i] * b[i];
  }
  return magA && magB ? dot / (Math.sqrt(magA) * Math.sqrt(magB)) : 0;
}

function blobToFloat32(buf: Buffer): Float32Array {
  return new Float32Array(buf.buffer, buf.byteOffset, buf.byteLength / 4);
}

// ── Query helpers ─────────────────────────────────────────────────────

const COLS = `id, content, confidence, category, status, decay_sensitivity, activation_score, last_relevant, updated_at, created_at`;

function loadCore(db: Database.Database, limit: number, agentId: string): BeliefRow[] {
  return db
    .prepare(
      `SELECT ${COLS} FROM beliefs
    WHERE status = 'active'
      AND confidence >= 0.3
      AND agent_id = ?
      AND (
        category = 'identity'
        OR category = 'goal'
        OR (category = 'preference' AND confidence > 0.80)
        OR (category = 'decision' AND confidence > 0.85 AND (decay_sensitivity = 'low' OR decay_sensitivity IS NULL))
        OR (category = 'fact' AND confidence > 0.90 AND (decay_sensitivity = 'low' OR decay_sensitivity IS NULL))
      )
    ORDER BY
      CASE category WHEN 'identity' THEN 0 WHEN 'goal' THEN 1 WHEN 'preference' THEN 2 WHEN 'decision' THEN 3 ELSE 4 END,
      confidence DESC
    LIMIT ?`,
    )
    .all(agentId, limit) as BeliefRow[];
}

function loadActive(db: Database.Database, limit: number, agentId: string): BeliefRow[] {
  return db
    .prepare(
      `SELECT ${COLS} FROM beliefs
    WHERE status = 'active'
      AND confidence >= 0.3
      AND agent_id = ?
      AND datetime(COALESCE(last_relevant, updated_at, created_at)) >= datetime('now', '-7 days')
    ORDER BY activation_score DESC, confidence DESC
    LIMIT ?`,
    )
    .all(agentId, limit * 5) as BeliefRow[];
}

function tokenize(prompt: string): string[] {
  return prompt
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 2)
    .slice(0, 12);
}

function loadRecalled(
  db: Database.Database,
  prompt: string,
  limit: number,
  agentId: string,
): BeliefRow[] {
  const tokens = tokenize(prompt);
  if (tokens.length === 0) return [];
  const where = tokens.map(() => "LOWER(content) LIKE ?").join(" OR ");
  const params = tokens.map((t) => `%${t}%`);
  return db
    .prepare(
      `SELECT ${COLS} FROM beliefs
    WHERE status = 'active'
      AND confidence >= 0.3
      AND agent_id = ?
      AND (${where})
    ORDER BY confidence DESC, activation_score DESC
    LIMIT ?`,
    )
    .all(agentId, ...params, limit * 4) as BeliefRow[];
}

async function loadRecalledSemantic(
  db: Database.Database,
  prompt: string,
  limit: number,
  apiKey: string,
  agentId: string,
): Promise<BeliefRow[]> {
  const promptVec = await generateEmbedding(prompt, apiKey);
  if (!promptVec) return loadRecalled(db, prompt, limit, agentId);

  const rows = db
    .prepare(
      `SELECT ${COLS}, embedding FROM beliefs
    WHERE status = 'active'
      AND confidence >= 0.3
      AND agent_id = ?
      AND embedding IS NOT NULL`,
    )
    .all(agentId) as (BeliefRow & { embedding: Buffer })[];

  if (rows.length === 0) return loadRecalled(db, prompt, limit, agentId);

  return rows
    .map((r) => ({ ...r, score: cosineSimilarity(promptVec, blobToFloat32(r.embedding)) }))
    .filter((r) => r.score > 0.3)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
}

// ── Dedup + tier assembly ─────────────────────────────────────────────

function buildTiered(
  core: BeliefRow[],
  active: BeliefRow[],
  recalled: BeliefRow[],
  cfg: BeeConfig,
): TieredBelief[] {
  const byId = new Map<string, TieredBelief>();
  const seenContent = new Set<string>();
  const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ");

  for (const r of core) {
    const k = norm(r.content);
    if (byId.has(r.id) || seenContent.has(k)) continue;
    byId.set(r.id, { ...r, tier: "core" });
    seenContent.add(k);
  }

  let ac = 0;
  for (const r of active) {
    if (ac >= cfg.maxActiveBeliefs) break;
    const k = norm(r.content);
    if (byId.has(r.id) || seenContent.has(k)) continue;
    byId.set(r.id, { ...r, tier: "active" });
    seenContent.add(k);
    ac++;
  }

  let rc = 0;
  for (const r of recalled) {
    if (rc >= cfg.maxRecalledBeliefs) break;
    const k = norm(r.content);
    if (byId.has(r.id) || seenContent.has(k)) continue;
    byId.set(r.id, { ...r, tier: "recalled" });
    seenContent.add(k);
    rc++;
  }

  return [...byId.values()];
}

// ── Formatting ────────────────────────────────────────────────────────

function daysAgo(ts: string | null): string {
  if (!ts) return "";
  const d = Math.floor((Date.now() - new Date(ts).getTime()) / 86400000);
  if (d <= 0) return "today";
  if (d < 7) return `${d}d ago`;
  return `${Math.floor(d / 7)}w ago`;
}

function fmtBelief(b: TieredBelief, includeTiming: boolean): string {
  const ts = includeTiming ? `, ${daysAgo(b.last_relevant ?? b.updated_at)}` : "";
  return `- [${b.category}, ${b.confidence.toFixed(2)}${ts}] ${b.content}`;
}

function formatOutput(beliefs: TieredBelief[], maxChars: number): string | null {
  const core = beliefs.filter((b) => b.tier === "core");
  const active = beliefs.filter((b) => b.tier === "active");
  const recalled = beliefs.filter((b) => b.tier === "recalled");

  if (core.length === 0 && active.length === 0 && recalled.length === 0) return null;

  const coreLines = core.map((b) => fmtBelief(b, false));
  const activeLines = active.map((b) => fmtBelief(b, true));
  const recalledLines = recalled.map((b) => fmtBelief(b, false));

  const build = () =>
    [
      "<bee-recall>",
      "## Core beliefs (always active)",
      ...(coreLines.length ? coreLines : ["- (none)"]),
      "",
      "## Active context (recent)",
      ...(activeLines.length ? activeLines : ["- (none)"]),
      "",
      "## Recalled (relevant to this message)",
      ...(recalledLines.length ? recalledLines : ["- (none)"]),
      "</bee-recall>",
    ].join("\n");

  let out = build();
  while (out.length > maxChars && recalledLines.length > 0) {
    recalledLines.pop();
    out = build();
  }
  while (out.length > maxChars && activeLines.length > 0) {
    activeLines.pop();
    out = build();
  }
  while (out.length > maxChars && coreLines.length > 0) {
    coreLines.pop();
    out = build();
  }

  return out.length > 0 ? out : null;
}

// ── LLM Extraction ────────────────────────────────────────────────────

const VALID_CATEGORIES = new Set(["identity", "goal", "preference", "decision", "fact"]);

function buildExtractionPrompt(messages: Array<{ role: string; text: string }>): string {
  let msgText = messages
    .filter((m) => m.role && m.text && m.text.trim().length > 0)
    .map((m) => `[${m.role}]: ${m.text.trim()}`)
    .join("\n");

  if (msgText.length > 2000) {
    msgText = msgText.slice(msgText.length - 2000);
    const nl = msgText.indexOf("\n");
    if (nl > 0) msgText = msgText.slice(nl + 1);
  }

  return `You are a belief extraction system. Analyze this conversation and extract durable beliefs — facts, preferences, decisions, goals, and identity information about the user that should be remembered long-term.

RULES:
- Only extract beliefs that are durable (not one-time commands or transient requests)
- Do NOT extract: commands ("add this to agents.md"), questions, temporary instructions, raw message text
- DO extract: preferences ("Chief prefers X"), decisions ("we decided to use Y"), goals ("building a $100M company"), facts about the user/project
- confidence: 0.75-0.95 for explicit statements, 0.55-0.74 for implied
- importance: 1-10 scale (1=mundane trivia, 5=useful context, 10=critical identity/mission fact)
  - Examples: "user's name is Kartik" → 9, "user prefers dark mode" → 4, "today's meeting was good" → 2
- Maximum 3 beliefs per call
- If nothing durable, return empty array

Conversation:
${msgText}

Respond with ONLY valid JSON (no markdown, no explanation):
{"beliefs": [{"content": "string", "category": "identity|goal|preference|decision|fact", "confidence": 0.0-1.0, "importance": 1-10, "reasoning": "why this is durable"}]}`;
}

function extractTextFromResponse(result: unknown): string | null {
  if (!result || typeof result !== "object") return null;
  const r = result as Record<string, unknown>;
  if (typeof r.text === "string") return r.text;
  if (typeof r.content === "string") return r.content;
  if (Array.isArray(r.choices) && r.choices.length > 0) {
    const msg = (r.choices[0] as Record<string, unknown>).message as
      | Record<string, unknown>
      | undefined;
    if (msg && typeof msg.content === "string") return msg.content;
  }
  return null;
}

function validateAndParseBeliefs(jsonStr: string, minConfidence: number): ExtractedBelief[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonStr);
  } catch {
    return [];
  }
  if (!parsed || typeof parsed !== "object") return [];
  const p = parsed as Record<string, unknown>;
  if (!Array.isArray(p.beliefs)) return [];

  const valid: ExtractedBelief[] = [];
  for (const item of p.beliefs as unknown[]) {
    if (!item || typeof item !== "object") continue;
    const b = item as Record<string, unknown>;
    if (typeof b.content !== "string") continue;
    const content = b.content.trim();
    if (content.length < 10 || content.length > 500) continue;
    if (typeof b.category !== "string" || !VALID_CATEGORIES.has(b.category)) continue;
    if (typeof b.confidence !== "number" || b.confidence < 0.5 || b.confidence > 1.0) continue;
    if (b.confidence < minConfidence) continue;
    const importance =
      typeof b.importance === "number" ? Math.min(10, Math.max(1, Math.round(b.importance))) : 5;
    valid.push({
      content,
      category: b.category,
      confidence: b.confidence,
      importance,
      reasoning: typeof b.reasoning === "string" ? b.reasoning : "",
    });
  }
  return valid;
}

// Returns the inserted belief ID (or existing ID if deduped)
function insertExtractedBelief(
  db: Database.Database,
  belief: ExtractedBelief,
  agentId: string,
  sessionKey: string,
): string | null {
  const id = randomBytes(16).toString("hex").toLowerCase();
  // Provisional gate: ALL extracted beliefs start provisional — never auto-promoted
  const result = db
    .prepare(
      `INSERT OR IGNORE INTO beliefs
        (id, content, confidence, importance, category, status, agent_id, source, reasoning, decay_sensitivity, source_labels, tags)
       VALUES (?, ?, ?, ?, ?, 'provisional', ?, ?, ?, 'medium', '[]', '[]')`,
    )
    .run(
      id,
      belief.content,
      belief.confidence,
      belief.importance,
      belief.category,
      agentId,
      `bee:${sessionKey}`,
      belief.reasoning,
    );
  return result.changes > 0 ? id : null;
}

// Async dedup check + embedding update (fire-and-forget)
function scheduleEmbedding(
  dbPath: string,
  beliefId: string,
  content: string,
  agentId: string,
  apiKey: string,
  logWarn: (msg: string) => void,
): void {
  if (!apiKey) return;
  void (async () => {
    try {
      const vec = await generateEmbedding(content, apiKey);
      if (!vec) return;
      const blob = Buffer.from(vec.buffer);

      // Check for near-duplicates (cosine > 0.92) in existing beliefs
      const db2 = new Database(dbPath);
      try {
        const existing = db2
          .prepare(
            `SELECT id, content, confidence, embedding FROM beliefs
             WHERE agent_id = ? AND embedding IS NOT NULL AND id != ? AND status = 'active'`,
          )
          .all(agentId, beliefId) as Array<{
          id: string;
          content: string;
          confidence: number;
          embedding: Buffer;
        }>;

        for (const row of existing) {
          const sim = cosineSimilarity(vec, blobToFloat32(row.embedding));
          if (sim > 0.92) {
            // Merge: update existing, delete new
            db2
              .prepare(
                `UPDATE beliefs SET confidence = MIN(confidence + 0.03, 1.0),
                   updated_at = datetime('now') WHERE id = ?`,
              )
              .run(row.id);
            db2.prepare(`DELETE FROM beliefs WHERE id = ?`).run(beliefId);
            logWarn(
              `bee: dedup — merged "${content.slice(0, 40)}" into existing belief ${row.id} (sim=${sim.toFixed(3)})`,
            );
            return;
          }
        }

        // No duplicate found — store embedding
        db2.prepare(`UPDATE beliefs SET embedding = ? WHERE id = ?`).run(blob, beliefId);
      } finally {
        db2.close();
      }
    } catch {
      /* ignore — embedding is best-effort */
    }
  })();
}

// ── Phase 1B: PM Cognition Block ─────────────────────────────────────
//
// buildPMCognitionBlock — loads private + shared beliefs for a PM agent
// and formats them as a structured <pm-cognition> block for injection.
// Wired in Phase 2: injected via before_agent_start for subagent paths.

interface PMBeliefRow {
  content: string;
  category: string;
  confidence: number;
  action_implication: string | null;
}

function buildPMCognitionBlock(db: Database.Database, agentId: string): string {
  const MAX_PRIVATE = 5;
  const MAX_SHARED = 3;

  let privateBeliefs: PMBeliefRow[] = [];
  let sharedBeliefs: PMBeliefRow[] = [];

  try {
    privateBeliefs = db
      .prepare(
        `SELECT content, category, confidence, action_implication
         FROM beliefs
         WHERE agent_id = ? AND status = 'active'
         ORDER BY activation_score DESC, importance DESC
         LIMIT ?`,
      )
      .all(agentId, MAX_PRIVATE) as PMBeliefRow[];
  } catch {
    /* table may not exist yet */
  }

  try {
    sharedBeliefs = db
      .prepare(
        `SELECT content, category, confidence, action_implication
         FROM beliefs
         WHERE agent_id = '__shared__' AND status = 'active'
         ORDER BY activation_score DESC, importance DESC
         LIMIT ?`,
      )
      .all(MAX_SHARED) as PMBeliefRow[];
  } catch {
    /* ignore */
  }

  const formatBelief = (b: PMBeliefRow): string => {
    const base = `- [${b.category}, ${b.confidence.toFixed(2)}] ${b.content}`;
    if (b.action_implication && b.action_implication.trim()) {
      return `${base}\n  → ${b.action_implication.trim()}`;
    }
    return base;
  };

  let block = "<pm-cognition>\n";

  block += "## Your beliefs (private)\n";
  if (privateBeliefs.length > 0) {
    block += privateBeliefs.map(formatBelief).join("\n") + "\n";
  } else {
    block += "No prior beliefs for this agent — forming from scratch.\n";
  }

  block += "\n## Shared context (from VECTOR)\n";
  if (sharedBeliefs.length > 0) {
    block += sharedBeliefs.map(formatBelief).join("\n") + "\n";
  } else {
    block += "No shared context available.\n";
  }

  block += "</pm-cognition>";
  return block;
}

// PM output format instruction injected into subagent prompts (Phase 2).
// Wired: appended to prependContext in before_agent_start for subagent paths.
const PM_OUTPUT_FORMAT_INSTRUCTION = `
<output-format>
At the END of your task response, include this JSON block (no markdown fences, raw JSON on its own line):
{"belief_updates":[{"content":"string","category":"identity|goal|preference|decision|fact","confidence":0.0-1.0,"importance":1-10,"action_implication":"what VECTOR should do differently based on this","evidence_for":"why this is true","evidence_against":"what would disprove this"}],"memory_operations":[{"op":"store|update|archive","content":"string","importance":1-10}],"proposals":[],"knowledge_gaps":[{"domain":"infrastructure|code|business|research","description":"what you don't know that matters"}]}
Only include belief_updates if you learned something genuinely durable. Empty arrays are fine.
proposals array is always empty for now (Phase 3).
knowledge_gaps: list things you encountered but couldn't confirm — these become tracked unknowns.
</output-format>
`.trim();

// Phase 2B: Build <bee-uncertainty> block for main session.
// Returns null if nothing noteworthy (no conflicts, no open gaps, fewer than 3 low-conf beliefs).
function buildUncertaintyBlock(db: Database.Database, agentId: string): string | null {
  const conflicts = db
    .prepare(`
    SELECT b1.content, b2.content as conflicts_with
    FROM beliefs b1 JOIN beliefs b2 ON b1.contradicts = b2.id
    WHERE b1.agent_id = ? AND b1.status = 'active' LIMIT 3
  `)
    .all(agentId) as Array<{ content: string; conflicts_with: string }>;

  const gaps = db
    .prepare(`
    SELECT domain, description FROM knowledge_gaps
    WHERE agent_id = ? AND resolved_at IS NULL
    ORDER BY importance DESC LIMIT 5
  `)
    .all(agentId) as Array<{ domain: string; description: string }>;

  const lowConf = db
    .prepare(`
    SELECT content, confidence, category FROM beliefs
    WHERE agent_id = ? AND status = 'active' AND confidence < 0.65
    ORDER BY importance DESC LIMIT 3
  `)
    .all(agentId) as Array<{ content: string; confidence: number; category: string }>;

  if (conflicts.length === 0 && gaps.length === 0 && lowConf.length < 3) return null;

  const lines: string[] = ["<bee-uncertainty>"];
  if (conflicts.length > 0) {
    lines.push("## Known conflicts");
    for (const c of conflicts) {
      lines.push(`- "${c.content.slice(0, 60)}" conflicts with "${c.conflicts_with.slice(0, 60)}"`);
    }
  }
  if (gaps.length > 0) {
    lines.push("## Known gaps");
    for (const g of gaps) {
      lines.push(`- [${g.domain}] ${g.description}`);
    }
  }
  if (lowConf.length >= 3) {
    lines.push("## Low-confidence beliefs (verify before acting)");
    for (const b of lowConf) {
      lines.push(`- [${b.category}, ${b.confidence.toFixed(2)}] ${b.content.slice(0, 80)}`);
    }
  }
  lines.push("</bee-uncertainty>");
  return lines.join("\n");
}

// ── processPMOutput: parse + persist PM belief_updates (mirrors update_beliefs.py) ──

interface PMOutputData {
  belief_updates?: unknown[];
  memory_operations?: unknown[];
  knowledge_gaps?: unknown[];
}

// Agent IDs that subagents (PMs/workers) may NOT write to — only VECTOR's main session may
const PROTECTED_AGENT_IDS = new Set(["vector", "__shared__"]);

function processPMOutput(db: Database.Database, agentId: string, data: PMOutputData): void {
  // Security: reject cross-agent writes — PMs cannot write to protected agent namespaces.
  // The agentId here MUST come from ctx.agentId (runtime session context), never from PM JSON output.
  if (PROTECTED_AGENT_IDS.has(agentId)) {
    // Allow only if called from main session context (non-subagent). Since session_end
    // already guards "if (!sessionId.includes('subagent')) return", this is belt-and-suspenders.
    // If somehow a subagent's agentId resolves to 'vector', silently drop to prevent poisoning.
    return;
  }

  const beliefUpdates = Array.isArray(data.belief_updates) ? data.belief_updates : [];
  const memOps = Array.isArray(data.memory_operations) ? data.memory_operations : [];
  const gaps = Array.isArray(data.knowledge_gaps) ? data.knowledge_gaps : [];

  let stored = 0;

  for (const raw of beliefUpdates) {
    if (!raw || typeof raw !== "object") continue;
    const b = raw as Record<string, unknown>;

    const content = typeof b.content === "string" ? b.content.trim() : "";
    if (content.length < 10 || content.length > 500) continue;

    let category = typeof b.category === "string" ? b.category : "fact";
    if (!VALID_CATEGORIES.has(category)) category = "fact";

    let confidence = typeof b.confidence === "number" ? b.confidence : 0.65;
    confidence = Math.max(0.5, Math.min(1.0, confidence));

    let importance = typeof b.importance === "number" ? b.importance : 5.0;
    importance = Math.max(1.0, Math.min(10.0, importance));

    const actionImpl =
      typeof b.action_implication === "string" ? b.action_implication.slice(0, 500) : "";
    const evidenceFor = typeof b.evidence_for === "string" ? b.evidence_for.slice(0, 500) : "";
    const evidenceAgainst =
      typeof b.evidence_against === "string" ? b.evidence_against.slice(0, 500) : "";

    const bid = `pm-${agentId.slice(0, 8)}-${randomBytes(4).toString("hex")}`;

    // All PM belief_updates are ALWAYS provisional — never inserted as active
    try {
      db.prepare(
        `INSERT OR IGNORE INTO beliefs
          (id, content, confidence, category, status, agent_id, source, importance,
           action_implication, evidence_for, evidence_against)
         VALUES (?,?,?,?,'provisional',?,?,?,?,?,?)`,
      ).run(
        bid,
        content,
        confidence,
        category,
        agentId,
        `pm_output:${agentId}`,
        importance,
        actionImpl,
        evidenceFor,
        evidenceAgainst,
      );
      stored++;
    } catch {
      /* ignore insert errors */
    }
  }

  // memory_operations
  for (const raw of memOps) {
    if (!raw || typeof raw !== "object") continue;
    const op = raw as Record<string, unknown>;
    const operation = typeof op.op === "string" ? op.op : "store";
    const content = typeof op.content === "string" ? op.content.trim() : "";
    const importance =
      typeof op.importance === "number" ? Math.max(1, Math.min(10, op.importance)) : 5;

    if (operation === "store" && content) {
      const mid = randomBytes(4).toString("hex");
      try {
        db.prepare(
          `INSERT OR IGNORE INTO memories (id, agent_id, content, importance, source)
           VALUES (?,?,?,?,?)`,
        ).run(mid, agentId, content, importance, `pm_memory_op:${agentId}`);
      } catch {
        /* ignore */
      }
    } else if (operation === "archive" && content) {
      try {
        db.prepare(`UPDATE beliefs SET status='archived' WHERE agent_id=? AND content=?`).run(
          agentId,
          content,
        );
      } catch {
        /* ignore */
      }
    }
  }

  // knowledge_gaps
  let gapsStored = 0;
  for (const raw of gaps) {
    if (!raw || typeof raw !== "object") continue;
    const g = raw as Record<string, unknown>;
    const domain = typeof g.domain === "string" ? g.domain.slice(0, 100) : "unknown";
    const description = typeof g.description === "string" ? g.description.trim() : "";
    if (!description || description.length < 10) continue;
    const importance =
      typeof g.importance === "number" ? Math.max(1, Math.min(10, g.importance)) : 5;
    const gid = randomBytes(4).toString("hex");
    try {
      db.prepare(
        `INSERT OR IGNORE INTO knowledge_gaps (id, agent_id, domain, description, importance)
         VALUES (?,?,?,?,?)`,
      ).run(gid, agentId, domain, description, importance);
      gapsStored++;
    } catch {
      /* ignore */
    }
  }

  // audit
  try {
    db.prepare(`INSERT INTO audit_log (agent, action, detail) VALUES (?,?,?)`).run(
      agentId,
      "pm_belief_update",
      JSON.stringify({ stored, memory_ops: memOps.length, gaps: gapsStored }),
    );
  } catch {
    /* ignore */
  }
}

// ── Plugin entry point ────────────────────────────────────────────────

const beePlugin = {
  id: "bee",
  name: "BEE (Belief Extraction Engine)",
  description:
    "Cognitive memory — extracts durable beliefs via LLM and injects them before every AI turn",
  kind: undefined,
  configSchema: { type: "object" as const, properties: {} },

  register(api: OpenClawPluginApi) {
    const cfg = parseConfig(api.pluginConfig ?? {});

    // Read OpenAI key once at startup (graceful — empty string = embeddings disabled)
    const openaiKey = readOpenAIKey();
    if (!openaiKey) {
      api.logger.warn("bee: OpenAI key not found — embeddings disabled, keyword fallback active");
    }

    // Verify DB exists
    let dbOk = false;
    try {
      const db = new Database(cfg.dbPath, { readonly: true });
      const tbl = db
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='beliefs'")
        .get();
      db.close();
      dbOk = !!tbl;
    } catch {
      dbOk = false;
    }

    if (!dbOk) {
      api.logger.warn(`bee: beliefs table not found at ${cfg.dbPath} — recall disabled`);
      return;
    }

    api.logger.info(
      `bee: v3 connected to ${cfg.dbPath} — recall active, extraction ${cfg.extractionEnabled ? "enabled" : "disabled"}, embeddings ${openaiKey ? "enabled" : "disabled"}`,
    );

    // ── gateway_start: schema migrations ──
    api.on("gateway_start", (_event) => {
      try {
        const db = new Database(cfg.dbPath);
        try {
          // Existing: agent_id column
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN agent_id TEXT NOT NULL DEFAULT 'vector'");
            api.logger.info("bee: migration — agent_id column added to beliefs");
          } catch {
            /* already exists */
          }

          // Phase 1: embedding BLOB on beliefs
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN embedding BLOB");
            api.logger.info("bee: migration — embedding column added to beliefs");
          } catch {
            /* already exists */
          }

          // Phase 1B: PM cognition columns
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN action_implication TEXT");
            api.logger.info("bee: migration — action_implication column added to beliefs");
          } catch {
            /* already exists */
          }
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN belief_type TEXT");
            api.logger.info("bee: migration — belief_type column added to beliefs");
          } catch {
            /* already exists */
          }
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN evidence_for TEXT");
            api.logger.info("bee: migration — evidence_for column added to beliefs");
          } catch {
            /* already exists */
          }
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN evidence_against TEXT");
            api.logger.info("bee: migration — evidence_against column added to beliefs");
          } catch {
            /* already exists */
          }

          // Phase 2B: metacognition columns
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN uncertainty_type TEXT");
            api.logger.info("bee: migration — uncertainty_type column added to beliefs");
          } catch {
            /* already exists */
          }
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN contradicts TEXT");
            api.logger.info("bee: migration — contradicts column added to beliefs");
          } catch {
            /* already exists */
          }
          try {
            db.exec("ALTER TABLE beliefs ADD COLUMN knowledge_gap TEXT");
            api.logger.info("bee: migration — knowledge_gap column added to beliefs");
          } catch {
            /* already exists */
          }

          // Phase 2B: knowledge_gaps table
          db.exec(`CREATE TABLE IF NOT EXISTS knowledge_gaps (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            domain TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            importance REAL NOT NULL DEFAULT 5.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
          )`);
          api.logger.info("bee: migration — knowledge_gaps table ready");

          // Fix 3: Stale provisional warning — alert VECTOR to trigger reflect.py
          try {
            const stale = db
              .prepare(`
              SELECT agent_id, COUNT(*) as cnt FROM beliefs
              WHERE status='provisional' AND created_at < datetime('now', '-1 day')
              GROUP BY agent_id HAVING cnt > 10
            `)
              .all() as Array<{ agent_id: string; cnt: number }>;
            for (const s of stale) {
              api.logger.warn(
                `bee: ${s.cnt} stale provisional beliefs for ${s.agent_id} — run reflect.py --agent ${s.agent_id}`,
              );
            }
          } catch {
            /* ignore — may fail if beliefs table not yet created */
          }

          // Phase 1: memories table
          db.exec(`CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL DEFAULT 'vector',
            content TEXT NOT NULL,
            importance REAL NOT NULL DEFAULT 5.0,
            decay_rate REAL NOT NULL DEFAULT 0.3,
            access_count INTEGER NOT NULL DEFAULT 0,
            last_accessed TEXT,
            activation_score REAL NOT NULL DEFAULT 0.0,
            embedding BLOB,
            source TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
          )`);
          api.logger.info("bee: migration — memories table ready");
        } finally {
          db.close();
        }
      } catch (err) {
        api.logger.warn(`bee: migration failed: ${String(err)}`);
      }
    });

    // ── session_start: audit subagent spawns + budget check ──
    api.on("session_start", (event, ctx) => {
      const sessionId = event.sessionId;
      const agentId = ctx.agentId ?? "unknown";
      const isSubagent = sessionId.includes("subagent");

      if (isSubagent) {
        try {
          const db = new Database(cfg.dbPath);
          try {
            db.prepare(`INSERT INTO audit_log (agent, action, detail) VALUES (?, 'spawn', ?)`).run(
              agentId,
              JSON.stringify({ sessionId, ts: new Date().toISOString() }),
            );
          } finally {
            db.close();
          }
        } catch (err) {
          api.logger.warn(`bee: audit spawn failed: ${String(err)}`);
        }

        try {
          const db = new Database(cfg.dbPath, { readonly: true });
          try {
            const row = db
              .prepare(
                `SELECT COUNT(*) as cnt FROM audit_log WHERE action='spawn' AND ts > datetime('now','-1 day')`,
              )
              .get() as { cnt: number };
            if (row.cnt > cfg.spawnBudgetWarning) {
              api.logger.warn(
                `bee: budget warning — ${row.cnt} spawns in last 24h (limit: ${cfg.spawnBudgetWarning})`,
              );
            }
          } finally {
            db.close();
          }
        } catch {
          /* ignore */
        }
      }
    });

    // ── session_end: audit subagent completion + auto-parse PM belief_updates ──
    api.on("session_end", (event, ctx) => {
      const sessionId = event.sessionId;
      const agentId = ctx.agentId ?? "unknown";
      if (!sessionId.includes("subagent")) return;

      // Audit completion
      try {
        const db = new Database(cfg.dbPath);
        try {
          db.prepare(
            `INSERT INTO audit_log (agent, action, detail, duration_seconds) VALUES (?, 'complete', ?, ?)`,
          ).run(
            agentId,
            JSON.stringify({ sessionId, messageCount: event.messageCount }),
            Math.round((event.durationMs ?? 0) / 1000),
          );
        } finally {
          db.close();
        }
      } catch (err) {
        api.logger.warn(`bee: audit complete failed: ${String(err)}`);
      }

      // Fix 2: Auto-parse PM belief_updates JSON from last session message
      if (event.messageCount > 0) {
        try {
          const msgs = (event as Record<string, unknown>).messages;
          if (Array.isArray(msgs) && msgs.length > 0) {
            const lastMsg = msgs[msgs.length - 1] as Record<string, unknown>;
            const text = String(lastMsg.text ?? lastMsg.content ?? "");

            // Find JSON block containing belief_updates (at end of message or on its own line)
            const jsonMatch =
              text.match(/\{"belief_updates"[\s\S]*?\}\s*$/m) ??
              text.match(/\n(\{"belief_updates"[\s\S]*\})\s*$/) ??
              text.match(/(\{"belief_updates"[\s\S]*\})/);

            if (jsonMatch) {
              try {
                const raw = jsonMatch[1] ?? jsonMatch[0];
                const parsed = JSON.parse(raw) as Record<string, unknown>;
                if (parsed.belief_updates || parsed.memory_operations || parsed.knowledge_gaps) {
                  const db2 = new Database(cfg.dbPath);
                  try {
                    processPMOutput(db2, agentId, parsed as PMOutputData);
                    api.logger.info(
                      `bee: auto-parsed PM belief_updates for ${agentId} (session=${sessionId})`,
                    );
                  } finally {
                    db2.close();
                  }
                }
              } catch {
                // Malformed JSON — silent skip (do not crash session_end)
              }
            }
          }
        } catch (err) {
          api.logger.warn(`bee: session_end belief parse error: ${String(err)}`);
        }
      }
    });

    // ── before_agent_start: inject beliefs + memories ──
    api.on("before_agent_start", async (event, ctx) => {
      const prompt = (event as { prompt?: string }).prompt;
      if (!prompt || prompt.length < 3) return;

      const sessionKey = ctx.sessionKey ?? "";
      const isSubagent = sessionKey.includes("subagent");
      // Main: cfg.agentId (usually 'vector'). Subagent: ctx.agentId.
      const agentId = isSubagent ? (ctx.agentId ?? null) : cfg.agentId;
      if (!agentId) return;

      let prependContext = "";

      // Wire PM cognition + output format for subagents (Phase 2)
      if (isSubagent && agentId) {
        try {
          const db = new Database(cfg.dbPath, { readonly: true });
          try {
            const pmBlock = buildPMCognitionBlock(db, agentId);
            const prependParts: string[] = [pmBlock, PM_OUTPUT_FORMAT_INSTRUCTION];
            return { prependContext: prependParts.join("\n\n") };
          } finally {
            db.close();
          }
        } catch (err) {
          api.logger.warn(`bee: PM cognition block failed: ${String(err)}`);
        }
        return; // skip main session recall for subagents
      }

      // Main session: inject beliefs (semantic recall if key available, else keyword)
      if (!isSubagent) {
        try {
          const db = new Database(cfg.dbPath, { readonly: true });
          try {
            const core = loadCore(db, cfg.maxCoreBeliefs, agentId);
            const active = loadActive(db, cfg.maxActiveBeliefs, agentId);
            const recalled = openaiKey
              ? await loadRecalledSemantic(db, prompt, cfg.maxRecalledBeliefs, openaiKey, agentId)
              : loadRecalled(db, prompt, cfg.maxRecalledBeliefs, agentId);
            const tiered = buildTiered(core, active, recalled, cfg);
            const context = formatOutput(tiered, cfg.maxOutputChars);

            if (cfg.debug) {
              api.logger.info(
                `bee: recall → core=${core.length} active=${active.length} recalled=${recalled.length} agent=${agentId} semantic=${!!openaiKey}`,
              );
            }
            if (context) prependContext += context;

            // Phase 2B: append uncertainty block if noteworthy
            const uncertaintyBlock = buildUncertaintyBlock(db, agentId);
            if (uncertaintyBlock) prependContext += `\n\n${uncertaintyBlock}`;
          } finally {
            db.close();
          }
        } catch (err) {
          api.logger.warn(`bee: recall error: ${String(err)}`);
        }
      }

      // PM (subagent) sessions: inject memories for that agent
      if (isSubagent && agentId !== cfg.agentId) {
        try {
          const db = new Database(cfg.dbPath, { readonly: true });
          try {
            const memories = db
              .prepare(
                `SELECT content, importance, activation_score FROM memories
                 WHERE agent_id IN (?, '__shared__')
                 ORDER BY activation_score DESC, importance DESC LIMIT 5`,
              )
              .all(agentId) as Array<{
              content: string;
              importance: number;
              activation_score: number;
            }>;

            if (memories.length > 0) {
              const memBlock = memories
                .map((m) => `- [importance=${m.importance.toFixed(0)}] ${m.content}`)
                .join("\n");
              prependContext += `\n\n<agent-memories>\n${memBlock}\n</agent-memories>`;
            }
          } finally {
            db.close();
          }
        } catch (err) {
          api.logger.warn(`bee: memory inject error: ${String(err)}`);
        }
      }

      if (prependContext) return { prependContext };
    });

    // ── agent_end: LLM extraction of durable beliefs ──
    api.on("agent_end", (event, ctx) => {
      if (!cfg.extractionEnabled) return;

      const sessionKey = ctx.sessionKey ?? "";
      const agentId = sessionKey.includes("subagent") ? null : cfg.agentId;
      if (!agentId) return;

      void (async () => {
        try {
          const rawMessages = Array.isArray(event.messages) ? event.messages : [];
          const messages = rawMessages
            .slice(-6)
            .map((m) => {
              const msg = m as Record<string, unknown>;
              const role = typeof msg.role === "string" ? msg.role : "";
              if (role === "tool") return null;
              if (Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0) return null;
              const text =
                typeof msg.content === "string"
                  ? msg.content
                  : typeof msg.text === "string"
                    ? msg.text
                    : "";
              if (!role || !text.trim()) return null;
              return { role, text };
            })
            .filter((m): m is { role: string; text: string } => m !== null);

          if (messages.length === 0) return;

          // Fix 1: Strip system metadata boilerplate that pollutes extraction
          const STRIP_PATTERNS = [
            /^Conversation info \(untrusted metadata\)/,
            /^\[System Message\]/,
            /^\[openclaw\]/,
            /^```json\s*\{[\s\S]*?"message_id"/,
            /^HEARTBEAT/,
          ];
          const cleanMessages = messages.filter((m) => {
            const t = m.text.trim();
            return !STRIP_PATTERNS.some((p) => p.test(t)) && t.length > 20 && t.length < 2000;
          });
          if (cleanMessages.length === 0) return;

          const prompt = buildExtractionPrompt(cleanMessages);

          const runtime = api.runtime as unknown as Record<string, unknown>;
          const generateText = runtime.generateText as
            | ((opts: { model: string; prompt: string }) => Promise<unknown>)
            | undefined;

          if (typeof generateText !== "function") {
            if (cfg.debug) api.logger.info("bee: generateText not available — extraction skipped");
            return;
          }

          const result = await generateText({ model: cfg.extractionModel, prompt });
          const text = extractTextFromResponse(result);
          if (!text) {
            api.logger.warn("bee: extraction — empty response from LLM");
            return;
          }

          const jsonStr = text
            .trim()
            .replace(/^```(?:json)?\s*/i, "")
            .replace(/\s*```$/i, "")
            .trim();

          const beliefs = validateAndParseBeliefs(jsonStr, cfg.extractionMinConfidence);
          if (beliefs.length === 0) {
            if (cfg.debug) api.logger.info(`bee: 0 durable beliefs (session=${sessionKey})`);
            return;
          }

          const db = new Database(cfg.dbPath);
          const insertedIds: Array<{ id: string; content: string }> = [];
          try {
            for (const belief of beliefs) {
              const id = insertExtractedBelief(db, belief, agentId, sessionKey);
              if (id) insertedIds.push({ id, content: belief.content });
            }
          } finally {
            db.close();
          }

          api.logger.info(
            `bee: extracted ${beliefs.length} provisional belief(s) session=${sessionKey} agent=${agentId}`,
          );

          if (cfg.debug) {
            for (const b of beliefs)
              api.logger.info(
                `bee:   → [${b.category}, ${b.confidence.toFixed(2)}, imp=${b.importance}] ${b.content.slice(0, 80)}`,
              );
          }

          // Fire-and-forget: generate embeddings + dedup check
          for (const { id, content } of insertedIds) {
            scheduleEmbedding(cfg.dbPath, id, content, agentId, openaiKey, (msg) =>
              api.logger.warn(msg),
            );
          }
        } catch (err) {
          api.logger.warn(`bee: extraction error: ${String(err)}`);
        }
      })();
    });
  },
};

export default beePlugin;
