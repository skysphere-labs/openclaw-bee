#!/usr/bin/env python3
"""
validate_agent_message.py — Three-tier validator for inter-agent communication.
Blood-brain barrier for Phase 3 agent cognition.

TIER 1: HARD BLOCK — reject, log to security_audit
TIER 2: SANITIZE — strip patterns, allow through, log
TIER 3: FLAG — allow, set requires_review=1, log

Importable: from validate_agent_message import validate_message
"""

import base64
import html
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/Users/acevashisth/.openclaw/workspace/state/vector.db")

# ── TIER 1: Hard Block Patterns (case-insensitive substring match) ─────────────
TIER1_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous",
    "ignore your instructions",
    "you are now",
    "act as if",
    "forget everything",
    "forget all previous",
    "jailbreak",
    "disregard all prior",
    "sk-ant-",
    "oat01-",
    "ATTACH DATABASE",
    "load_extension(",
    "<bee-recall>",
    "INSERT INTO beliefs",
    "UPDATE beliefs",
    "DELETE FROM beliefs",
    "DROP TABLE",
    "PRAGMA key",
    "PRAGMA writable_schema",
    "Daily Chai",
    "-5251152994",
    "VECTOR writes",
    "VECTOR should",
    "openclaw gateway restart",
    "openclaw agent",
    "before_agent_start injection",
    "after_agent_end injection",
]

# ── TIER 2: Sanitize Patterns (regex substitution) ────────────────────────────
# Each entry: (compiled_regex, replacement, description)
TIER2_PATTERNS = [
    (
        re.compile(r'\boverride\b(?!\s+(?:class|def|function|method))', re.IGNORECASE),
        '[SANITIZED]',
        "standalone 'override' keyword",
    ),
    (
        re.compile(r'\bbypass\b(?!\s+(?:cache|cdn|proxy))', re.IGNORECASE),
        '[SANITIZED]',
        "standalone 'bypass' keyword",
    ),
    (
        # Strip '--' SQL comments that are NOT inside code blocks (```...```)
        # Strategy: only strip '--' that appear outside triple-backtick sections
        re.compile(r'--(?=[^\n]*$)', re.MULTILINE),
        '[SQL_COMMENT_STRIPPED]',
        "SQL comment operator '--'",
    ),
    (
        re.compile(r'sqlite_master', re.IGNORECASE),
        '[SCHEMA_TABLE]',
        "sqlite_master table reference",
    ),
]

# ── TIER 3: Flag for Review Patterns (regex, requires_review=1) ───────────────
TIER3_PATTERNS = [
    # Match belief_updates: or "belief_updates": (JSON key format)
    (re.compile(r'belief_updates["\s]*:', re.IGNORECASE), "belief_updates trigger"),
    (re.compile(r'confidence\s*:\s*1\.0', re.IGNORECASE), "max confidence injection"),
    (re.compile(r'"confidence"\s*:\s*1\.0', re.IGNORECASE), "max confidence JSON"),
    (re.compile(r'"status"\s*:\s*"active"', re.IGNORECASE), "status:active injection"),
    (re.compile(r'seed:MEM-', re.IGNORECASE), "seed belief impersonation"),
    (re.compile(r'"agent_id"\s*:\s*"vector"', re.IGNORECASE), "VECTOR namespace injection"),
    (re.compile(r'"agent_id"\s*:\s*"__shared__"', re.IGNORECASE), "shared namespace injection"),
]

# Protected agent IDs that cannot send messages as themselves
PROTECTED_AGENT_IDS = {"vector", "__shared__"}

# Max content length for validation (not for post_proposal — that has its own)
MAX_CONTENT_LENGTH = 10_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _log_security_audit(
    conn: sqlite3.Connection,
    agent: str,
    violation_type: str,
    detail: str,
    severity: str = "HIGH",
    response_taken: str = "BLOCKED",
) -> None:
    """Write a Tier 1 block event to security_audit table."""
    conn.execute(
        """
        INSERT INTO security_audit (ts, agent, violation_type, detail, severity, response_taken)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (_now_iso(), agent, violation_type, detail, severity, response_taken),
    )
    conn.commit()


# ── Homoglyph / confusable character map ─────────────────────────────────────
# Maps visually confusable non-ASCII characters to their ASCII equivalents.
# Covers the most common Cyrillic and Greek lookalikes used in prompt injection.
_CONFUSABLE_MAP: dict[str, str] = {
    # ── Cyrillic lowercase
    '\u0430': 'a',   # а → a
    '\u0435': 'e',   # е → e
    '\u043E': 'o',   # о → o
    '\u0440': 'p',   # р → p  (looks like Latin r, phonetically r — map to p as visual match)
    '\u0441': 'c',   # с → c
    '\u0445': 'x',   # х → x
    '\u0456': 'i',   # і → i  (Ukrainian і — the key homoglyph in test case a1)
    '\u0457': 'i',   # ї → i  (Ukrainian ї)
    '\u0455': 's',   # ѕ → s  (Cyrillic ѕ — test case a9)
    '\u0443': 'y',   # у → y  (looks like y)
    '\u0446': 'u',   # ц → u  (loose lookalike)
    # ── Cyrillic uppercase
    '\u0410': 'A',   # А → A
    '\u0412': 'B',   # В → B
    '\u0415': 'E',   # Е → E
    '\u041A': 'K',   # К → K
    '\u041C': 'M',   # М → M
    '\u041D': 'H',   # Н → H
    '\u041E': 'O',   # О → O
    '\u0420': 'P',   # Р → P  (looks like Latin R)
    '\u0421': 'C',   # С → C
    '\u0422': 'T',   # Т → T
    '\u0425': 'X',   # Х → X
    # ── Greek lowercase
    '\u03B1': 'a',   # α → a
    '\u03B5': 'e',   # ε → e
    '\u03BF': 'o',   # ο → o
    '\u03BD': 'v',   # ν → v
    '\u03C1': 'p',   # ρ → p  (looks like p/r)
    # ── Greek uppercase
    '\u0391': 'A',   # Α → A
    '\u0392': 'B',   # Β → B
    '\u0395': 'E',   # Ε → E
    '\u039F': 'O',   # Ο → O
    '\u03A1': 'P',   # Ρ → P
    # ── Miscellaneous lookalikes
    '\u01A0': 'O',   # Ơ → O
    '\u0458': 'j',   # ј → j  (Cyrillic je)
    '\u0501': 'd',   # Ԁ → d
}

# Leet-speak translation table: digit → typical letter substitution
_LEET_TABLE = str.maketrans('013457@$', 'oieasts$')


def normalize_content(text: str) -> str:
    """
    Normalize content BEFORE any pattern matching to defeat obfuscation.

    Attack vectors defeated:
      1. HTML/XML entities         — html.unescape (&#105; → 'i')
      2. Unicode homoglyphs        — NFKC + confusable map (Cyrillic 'і' → 'i')
      3. Zero-width/invisible chars — replaced with SPACE (preserves word boundaries)
      4. Null bytes                — replaced with SPACE
      5. Whitespace normalization  — collapses newline/tab splits into single space
      6. Base64 encoded payloads   — decoded and appended for Tier1 matching
      (Leet-speak is handled in the Tier1 check via _LEET_TABLE, not here)

    Security: called on ALL content, from_agent, and to_agent fields BEFORE Tier1/2/3 checks.
    """
    # 1. HTML/XML entity decode: &#105; → 'i', &lt; → '<', &amp; → '&', etc.
    text = html.unescape(text)

    # 2. NFKC normalization — collapses compatibility forms (fullwidth, fractions, etc.)
    text = unicodedata.normalize('NFKC', text)

    # 3. Apply homoglyph/confusable map (Cyrillic, Greek → Latin ASCII)
    #    This runs AFTER NFKC so we don't double-process decomposed forms.
    text = ''.join(_CONFUSABLE_MAP.get(c, c) for c in text)

    # 4. Replace zero-width / invisible characters with a SPACE
    #    Using SPACE (not empty string) preserves word boundaries so that
    #    'ignore\u200bprevious' → 'ignore previous' and matches Tier1.
    #    Covers: ZWSP, ZWNJ, ZWJ, BOM, LRM, RLM, LRE, RLE, PDF, LRO, RLO,
    #    WJ, FAP, IT, IS, IP, IAA, IAS, IAT
    text = re.sub(
        r'[\u200b\u200c\u200d\ufeff\u200e\u200f\u202a-\u202e\u2060-\u2064\uFFF9-\uFFFB]',
        ' ',
        text,
    )

    # 5. Replace null bytes with SPACE
    text = text.replace('\x00', ' ')

    # 6. Normalize whitespace: collapse runs of whitespace to single space,
    #    strip leading/trailing. Catches tab-separated and newline-split tricks.
    text = re.sub(r'\s+', ' ', text).strip()

    # 7. Detect and append decoded base64 blobs so Tier1 matches on decoded payload.
    #    Threshold: ≥32 base64 chars (≥24 bytes decoded) — high enough to avoid false
    #    positives on normal text, low enough to catch short encoded payloads.
    #    No word-boundary anchors — '=' is not a word char so \b fails at padded ends.
    b64_pattern = re.compile(r'[A-Za-z0-9+/]{32,}={0,2}')
    for match in b64_pattern.finditer(text):
        try:
            raw = match.group(0).rstrip('=')
            padded = raw + '=' * ((4 - len(raw) % 4) % 4)
            decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
            if decoded.strip():
                text += ' __BASE64_DECODED__: ' + decoded
        except Exception:
            pass

    return text


def _strip_code_blocks(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """
    Remove code blocks from text for Tier 2 '--' stripping.
    Returns (text_without_code_blocks, [(start, end, original_block), ...])
    so we can put them back after sanitization.
    """
    blocks = []
    pattern = re.compile(r'```.*?```', re.DOTALL)
    result = text
    for m in reversed(list(pattern.finditer(text))):
        placeholder = f"\x00CODEBLOCK{len(blocks)}\x00"
        blocks.append((m.start(), m.end(), m.group(0)))
        result = result[: m.start()] + placeholder + result[m.end() :]
    return result, blocks


def _restore_code_blocks(text: str, blocks: list[tuple[int, int, str]]) -> str:
    """Put code blocks back after Tier 2 sanitization."""
    for i, (_, _, original) in enumerate(blocks):
        placeholder = f"\x00CODEBLOCK{i}\x00"
        text = text.replace(placeholder, original)
    return text


def validate_message(content: str, from_agent: str, to_agent: str) -> dict:
    """
    Three-tier validation of an inter-agent message.

    Returns:
        {
            allowed: bool,
            blocked: bool,
            sanitized_content: str,
            requires_review: bool,
            violations: list[str],
            log: list[str],
        }
    """
    log: list[str] = []
    violations: list[str] = []
    blocked = False
    blocked_reason: str | None = None
    requires_review = False

    # ── PRE-PROCESSING: Normalize ALL fields before any checks ────────────────
    # This defeats homoglyph, zero-width, null-byte, split-line, and base64 tricks.
    content = normalize_content(content)
    from_agent = normalize_content(from_agent)
    to_agent = normalize_content(to_agent)
    log.append("[PRE] normalize_content() applied to content, from_agent, to_agent")

    # ── TIER 1: Hard Block ────────────────────────────────────────────────────
    # Comprehensive check across four derived views to defeat obfuscation:
    #  (a) normalized text              — catches homoglyphs, ZWS, entities, base64
    #  (b) leet-denormalized text       — catches '1gn0r3' → 'ignore'
    #  (c) whitespace-stripped text     — catches word-split across lines/chars
    #  (d) leet-denorm + no-space       — catches combined leet + split attacks
    content_lower = content.lower()
    content_leet = content_lower.translate(_LEET_TABLE)
    content_nospace = re.sub(r'\s+', '', content_lower)
    content_leet_nospace = re.sub(r'\s+', '', content_leet)

    for pattern in TIER1_PATTERNS:
        pattern_lower = pattern.lower()
        pattern_nospace = re.sub(r'\s+', '', pattern_lower)
        if (
            pattern_lower in content_lower
            or pattern_lower in content_leet
            or pattern_nospace in content_nospace
            or pattern_nospace in content_leet_nospace
        ):
            blocked = True
            violation = f"TIER1_BLOCK: matched pattern '{pattern}'"
            violations.append(violation)
            log.append(f"[TIER1] BLOCKED — {violation}")
            if blocked_reason is None:
                blocked_reason = f"Tier 1 pattern: '{pattern}'"

    if blocked:
        # Write ALL violations to security_audit
        try:
            conn = _get_db()
            detail = json.dumps({
                "from": from_agent,
                "to": to_agent,
                "violations": violations,
                "content_preview": content[:200],
            })
            _log_security_audit(
                conn,
                agent=from_agent,
                violation_type="TIER1_AGENT_MESSAGE_BLOCK",
                detail=detail,
                severity="CRITICAL",
                response_taken="BLOCKED_NOT_STORED",
            )
            conn.close()
        except Exception as e:
            log.append(f"[TIER1] WARNING: could not write to security_audit: {e}")

        return {
            "allowed": False,
            "blocked": True,
            "sanitized_content": "",
            "requires_review": False,
            "violations": violations,
            "log": log,
        }

    # ── TIER 2: Sanitize ──────────────────────────────────────────────────────
    sanitized = content
    # For '--' stripping: protect code blocks first
    sanitized_no_code, code_blocks = _strip_code_blocks(sanitized)

    for (regex, replacement, description) in TIER2_PATTERNS:
        if description == "SQL comment operator '--'":
            # Only strip from non-code-block regions
            new_text, n = regex.subn(replacement, sanitized_no_code)
            if n > 0:
                sanitized_no_code = new_text
                log.append(f"[TIER2] SANITIZED {n}x — {description}")
                violations.append(f"TIER2_SANITIZED: {description}")
        else:
            new_text, n = regex.subn(replacement, sanitized)
            if n > 0:
                sanitized = new_text
                log.append(f"[TIER2] SANITIZED {n}x — {description}")
                violations.append(f"TIER2_SANITIZED: {description}")

    # Restore code blocks after '--' stripping
    sanitized_no_code = _restore_code_blocks(sanitized_no_code, code_blocks)

    # Merge: the sanitized_no_code has '--' stripped, sanitized has other T2 applied
    # We need to apply '--' result back to sanitized
    # Strategy: apply the non-code '--' strip to the current sanitized value
    sanitized_no_code2, code_blocks2 = _strip_code_blocks(sanitized)
    sql_comment_re = re.compile(r'--(?=[^\n]*$)', re.MULTILINE)
    sanitized_no_code2_result, n2 = sql_comment_re.subn('[SQL_COMMENT_STRIPPED]', sanitized_no_code2)
    if n2 > 0:
        sanitized_no_code2_result = _restore_code_blocks(sanitized_no_code2_result, code_blocks2)
        sanitized = sanitized_no_code2_result
        # Remove duplicate log entry (already added above if n > 0)
    else:
        sanitized = _restore_code_blocks(sanitized_no_code2, code_blocks2)

    # ── TIER 3: Flag for Review ───────────────────────────────────────────────
    for (regex, description) in TIER3_PATTERNS:
        if regex.search(sanitized):
            requires_review = True
            log.append(f"[TIER3] FLAG — {description}")
            violations.append(f"TIER3_FLAG: {description}")

    return {
        "allowed": True,
        "blocked": False,
        "sanitized_content": sanitized,
        "requires_review": requires_review,
        "violations": violations,
        "log": log,
    }


# ── CLI entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate an inter-agent message")
    parser.add_argument("--content", required=True, help="Message content")
    parser.add_argument("--from-agent", required=True, dest="from_agent")
    parser.add_argument("--to-agent", required=True, dest="to_agent")
    args = parser.parse_args()

    result = validate_message(args.content, args.from_agent, args.to_agent)
    print(json.dumps(result, indent=2))
