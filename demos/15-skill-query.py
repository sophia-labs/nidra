"""Parity: the nvidia-ontology-query skill's sequence, end to end:
check_readiness -> search_terms -> check_answerable -> ask_question ->
validate the claim-bearing answer (references/query-validation.md)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, ask, chinook, has_number, nidra, show_answer, upstream_readiness  # noqa: E402

d = Demo("15-skill-query", "the query skill's sequence, with answer validation",
         "readiness, then the vocabulary, then a cheap coverage check, then the question, then validate SQL, rows, "
         "truncation and prose before presenting a claim.",
         "skills/nvidia-ontology-query/SKILL.md § Prefer MCP for reads; references/query-validation.md")
Q = "How many songs are longer than five minutes?"

d.section("1. check_readiness")
_, st = nidra("GET", "/api/semantic-compilation/status")
_, cn = nidra("GET", "/api/connections")
_, db = nidra("GET", "/api/datasources/dbs")
v = upstream_readiness(st, cn, db)
d.log(f"ready={v['ready']} built={v['semantic_layer_built']} can_execute_sql={v['can_execute_sql']} databases={v['databases']}")
d.log("(the skill would stop here: the fold reports a false blocker for an env-configured connection, demo 01; we go on)")

d.section("2. search_terms for the nouns: 'song' -> ?")
code, body = nidra("GET", "/api/terms", params={"query": "song"})
d.log(f"query=song -> {[t['name'] for t in body.get('terms', [])]}")
code, body = nidra("GET", "/api/terms", params={"query": "track"})
tr = body.get("terms", [])
d.log(f"query=track -> {[(t['name'], t['synonyms'], t['description']) for t in tr]}")
d.check(any(t["name"] == "Track" and "song" in t["synonyms"] for t in tr), "the glossary says 'song' is a synonym of Track")

d.section("3. check_answerable")
code, body = nidra("POST", "/api/question-entity-coverage", json={"question": Q}, timeout=180)
d.show(f"coverage ({code})", body, 1200)

d.section("4. ask_question")
a = ask(Q)
show_answer(d, a)

d.section("5. validate the claim (query-validation.md: population, measure, unit, rows, truncation, prose)")
if "LLM failed" in str(a.get("answer_text")):
    d.note("not graded: the model call failed (OpenRouter 402, the key's credit ran out tonight)")
    d.finish("not graded", "steps 1-3 run; the ask could not run (model credit exhausted)")
    raise SystemExit
sql = (a.get("final_sql") or "")
truth = chinook("SELECT count(*) FROM Track WHERE Milliseconds > 300000")[0][0]
d.check("Milliseconds" in sql and "300000" in sql.replace(",", ""), "measure and unit: five minutes became Milliseconds > 300000")
d.check("Track" in sql and "JOIN" not in sql.upper(), "population: every Track row, no join that could drop or duplicate rows")
d.check(len(a.get("rows") or []) == 1, "one row, not truncated")
d.check(has_number(a.get("rows") or [], truth, 0), f"the number matches the source ({truth})")
d.log("caveat a validator should still raise: the glossary defines Track as 'a single piece of recorded music', but 214 of "
      "Chinook's 3503 tracks are MPEG-4 video files; the count includes them (see demo 22)")
d.finish()
