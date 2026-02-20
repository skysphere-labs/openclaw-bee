#!/usr/bin/env python3
"""Hardcoded scope routing rules for PM belief/memory content."""

from __future__ import annotations

import re
from typing import Any

VALID_SCOPES = {"private", "shared", "global"}
PRIVATE_TOOL_KEYWORDS = ["tsx", "pnpm", "webpack", "jest", "pytest", "vitest", "eslint", "tsc --noEmit"]
SHARED_KEYWORDS = ["trpc", "gateway", "tunnel", "cloudflared", "vector.db", "18789"]
GLOBAL_PHRASES = ["chief prefer", "chief want", "chief said", "chief's preference"]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _contains_any(content_lower: str, terms: list[str]) -> bool:
    return any(term in content_lower for term in terms)


def route_scope(content: Any, agent_id: Any, declared_scope: Any) -> dict:
    """
    Returns: {resolved_scope, override_applied, rule_matched}
    Never raises.
    """
    try:
        c = str(content or "")
        c_lower = c.lower()
        aid = str(agent_id or "").lower().strip()
        declared = str(declared_scope or "").lower().strip()
        if declared not in VALID_SCOPES:
            declared = "private"

        # FORCE GLOBAL (order 1 — highest priority: Chief-level or system-wide)
        if _contains_any(c_lower, GLOBAL_PHRASES):
            resolved = "global"
            rule = "force_global:chief_preference_phrase"
        elif ("never deploy" in c_lower) or ("always deploy" in c_lower) or (
            "deploy on" in c_lower and any(day in c_lower for day in DAYS)
        ):
            resolved = "global"
            rule = "force_global:deployment_policy_phrase"
        elif ("kartik" in c_lower) or ("chief's" in c_lower):
            resolved = "global"
            rule = "force_global:chief_name_reference"
        elif ("vector should" in c_lower) or ("vector must" in c_lower):
            resolved = "global"
            rule = "force_global:vector_instruction"

        # FORCE SHARED (order 2 — cross-PM infrastructure facts)
        elif re.search(r"\bport\s+\d{4,5}\b", c_lower):
            resolved = "shared"
            rule = "force_shared:port_pattern"
        elif _contains_any(c_lower, SHARED_KEYWORDS):
            resolved = "shared"
            rule = "force_shared:infra_keyword"
        elif ("all pms should" in c_lower) or ("every agent" in c_lower) or ("all agents" in c_lower):
            resolved = "shared"
            rule = "force_shared:cross_agent_phrase"

        # FORCE PRIVATE (order 3 — PM-specific, self-referential, tool-specific)
        elif aid and aid in c_lower:
            resolved = "private"
            rule = "force_private:agent_id_in_content"
        elif _contains_any(c_lower, [k.lower() for k in PRIVATE_TOOL_KEYWORDS]):
            resolved = "private"
            rule = "force_private:tool_keyword"
        elif ("my preference" in c_lower) or ("i prefer" in c_lower) or ("for me" in c_lower):
            resolved = "private"
            rule = "force_private:self_preference_phrase"

        # DEFAULT
        else:
            resolved = declared
            rule = "default:declared_scope"

        return {
            "resolved_scope": resolved,
            "override_applied": resolved != declared,
            "rule_matched": rule,
        }
    except Exception as e:
        return {
            "resolved_scope": "private",
            "override_applied": True,
            "rule_matched": f"error_fallback:{type(e).__name__}",
        }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Route scope for a belief/memory content string")
    parser.add_argument("--content", default="")
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--declared-scope", default="private")
    args = parser.parse_args()
    print(json.dumps(route_scope(args.content, args.agent_id, args.declared_scope)))
