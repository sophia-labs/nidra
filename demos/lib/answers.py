"""One engine answer for the memory and trace demos: asked fresh (or reused with
DEMOS_REUSE_ANSWERS=1); when the model call fails, the answer recorded by demo
14 in demos/out/14-mcp-tools.txt is used instead, and the record says so."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from demo import OUT, ask

QUESTION = "How many songs are longer than five minutes?"


def recorded(path: str = os.path.join(OUT, "14-mcp-tools.txt")) -> dict[str, Any] | None:
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    part = text.split("## ask_question", 1)[-1]
    run_at = re.search(r"^run at (\S+)", text, re.M)
    q = re.search(r"^question: (.*)$", part, re.M)
    sql = re.search(r"^sql: (.*)$", part, re.M)
    steps = re.search(r"^steps: (.*)$", part, re.M)
    ans = re.search(r"^answer: (.*)$", part, re.M)
    rows = re.search(r"^rows \(\d+, first \d+\):\n(.*?)\n(?=answer:|CHECK|row_count)", part, re.M | re.S)
    if not (q and sql and rows):
        return None
    return {"question": q.group(1), "final_sql": sql.group(1), "rows": json.loads(rows.group(1)),
            "steps": [{"node": s.strip()} for s in (steps.group(1).split("→") if steps else [])],
            "answer_text": ans.group(1) if ans else "", "engine": "http://127.0.0.1:3001",
            "source": f"recorded by demo 14 at {run_at.group(1) if run_at else '?'} (demos/out/14-mcp-tools.txt)"}


def get_answer() -> dict[str, Any]:
    a = ask(QUESTION)
    if a.get("rows") and a.get("final_sql"):
        a["source"] = f"asked at {a.get('reused_from') or a.get('asked_at')}" + (" (reused)" if a.get("reused_from") else "")
        return a
    rec = recorded()
    if rec:
        rec["fresh_attempt"] = a.get("answer_text") or a.get("error")
        return rec
    return a
