#!/usr/bin/env python3
"""Expand a PM task description into retrieval keywords using openclaw agent.

Usage:
  python3 expand_retrieval_query.py --task "fix JWT auth bug" --agent forge

Output:
  JSON array of keywords only.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path('/Users/acevashisth/.openclaw/workspace/state/vector.db')


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(agent: str, task: str, mode: str, detail: str = '') -> None:
    preview = (task or '')[:80]
    msg = f"retrieval_query_expansion mode={mode} task='{preview}'"
    if detail:
        msg = f"{msg} detail={detail[:180]}"
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            'INSERT INTO audit_log (ts, agent, action, detail) VALUES (?, ?, ?, ?)',
            (_now_iso(), agent, 'query_expansion', msg),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def fallback_keywords(task: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_]{5,}", task or "")
    out = []
    seen = set()
    for w in words:
        k = w.lower()
        if k not in seen and k not in {'issue', 'fixing', 'fixed'}:
            seen.add(k)
            out.append(k)
    if not out and task and task.strip():
        out = [task.strip().split()[0].lower()]
    return out


def _extract_json_array(text: str) -> list[str] | None:
    if not text:
        return None
    m = re.search(r"\[[\s\S]*?\]", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    cleaned = []
    seen = set()
    for item in data:
        if not isinstance(item, str):
            continue
        k = item.strip().lower()
        if not k:
            continue
        if k not in seen:
            seen.add(k)
            cleaned.append(k)
    return cleaned


def expand_retrieval_query(task: str, agent_id: str) -> list[str]:
    prompt = (
        f'Given this task for agent {agent_id}: "{task}"\n\n'
        'List 5-8 specific keywords to search for relevant memories and beliefs.\n'
        'Focus on: technical terms, concepts, related topics, potential failure modes.\n'
        'Be specific — not generic words like "fix" or "issue".\n'
        'Return ONLY a JSON array of strings: ["keyword1", "keyword2", ...]\n'
        'No explanation, no markdown, just the JSON array.'
    )

    try:
        session_id = str(uuid.uuid4())
        r = subprocess.run(
            ['openclaw', 'agent', '--json', '--session-id', session_id, '--message', prompt],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if r.returncode != 0:
            raise RuntimeError(f'openclaw agent rc={r.returncode}: {r.stderr[:160]}')

        data = json.loads(r.stdout)
        payloads = data.get('result', {}).get('payloads', [])
        text = payloads[0].get('text', '') if payloads else ''
        keywords = _extract_json_array(text)
        if keywords:
            _log(agent_id, task, 'api')
            return keywords
        raise ValueError('API response missing parseable JSON array')
    except Exception as e:
        fb = fallback_keywords(task)
        _log(agent_id, task, 'fallback', str(e))
        return fb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--task', required=True)
    ap.add_argument('--agent', required=True)
    args = ap.parse_args()

    kws = expand_retrieval_query(args.task, args.agent)
    print(json.dumps(kws))
    return 0


if __name__ == '__main__':
    sys.exit(main())
