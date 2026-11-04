"""Parity: check_answerable (POST /api/question-entity-coverage), and what
ask_question does with a question the data cannot answer."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import GRAPH, MCP, Demo, ask, nidra, show_answer  # noqa: E402

d = Demo("08-check-answerable", "check_answerable and an unanswerable question",
         "check_answerable grades semantic-layer coverage of a question without running the agent; "
         "an unanswerable question shows up as low coverage, not as a confident wrong number.",
         "docs/mcp.md (check_answerable); skills/nvidia-ontology-query § Instructions")

cases = [
    ("glossary-covered (Pagila)", "How many patrons rented a film on May 25, 2022?", "high"),
    ("answerable but uncompiled (Chinook has only one term, Track, with no column attributes)", "How many tracks are there?", "any"),
    ("not in the data at all", "What was the weather in Montevideo on the day of each rental?", "low"),
    ("not in the data at all", "How many employees have a pension plan with the company?", "low"),
]
cov = {}
for label, q, expect in cases:
    d.section(f"{label}: {q}")
    code, body = nidra("POST", "/api/question-entity-coverage", json={"question": q}, timeout=180)
    d.show(f"POST /api/question-entity-coverage ({code})", body, 2500)
    data = (body or {}).get("data") or {}
    c = data.get("coverage")
    cov[q] = c
    d.log(f"coverage = {c}; uncovered = {data.get('uncovered_entities')}")
    if expect == "high":
        d.check(c is not None and c >= 0.75, f"covered question grades high ({c})")
    elif expect == "low":
        d.check(c is not None and c < 0.75, f"unanswerable question grades low ({c})")
if cov.get("How many tracks are there?") == 0.0:
    d.note("coverage grades the semantic layer only (column attributes, SQL attributes, custom analyses): "
           "'How many tracks are there?' grades 0.0 although ask_question answers it correctly (3503, demo 03) "
           "from the raw tables, because Chinook was never compiled (demo 11)")

d.section("why unanswerable questions grade 1.0 here: find's scores never fall below the coverage threshold")
m = MCP()
for q in ("weather in Montevideo", "zzqx flibbertigibbet", "rental"):
    ok, v = m.call("find", {"graphId": GRAPH, "query": q, "k": 3})
    hits = [(h["label"], h["score"], round(1 - h["score"], 2)) for h in (v.get("hits") or [])]
    d.log(f"  find {q!r}: (label, score, distance=1-score) {hits}")
d.note("Nidra's vector store turns find's score into a distance as 1 - score (auto_ontology/vdb/gardend.py:196), and "
       "find's fused score for its top hit is about 0.7 even for nonsense (multilingual-e5-small cosines sit near 0.8 "
       "for any pair), so every extracted entity lands under coverage_grade's max_distance=0.75 and counts as covered. "
       "check_answerable cannot say no on Nidra until the distance is calibrated (e.g. an absolute cosine floor).")

d.section("ask_question on the unanswerable question (live engine)")
a = ask("What was the weather in Montevideo on the day of each rental?")
show_answer(d, a)
if "LLM failed" in str(a.get("answer_text")):
    d.note("not graded: the model call failed (OpenRouter 402 'in_flight_budget_exhausted': the key's credit ran out "
           "tonight), so 'LLM failed to produce a response.' says nothing about how the agent treats the question")
else:
    refused = not a.get("rows") and not (a.get("final_sql") or "").strip()
    d.check(refused, "the agent declines instead of inventing SQL")
d.finish()
