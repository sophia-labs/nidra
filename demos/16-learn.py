"""Parity: Learn: fold certified corrections and usage back into the ontology."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import FORK, MCP, Demo, nidra, sparql, term  # noqa: E402

d = Demo("16-learn", "Learn: certified corrections and usage folded back",
         "Learn: fold certified corrections and usage back into the ontology, so shared context improves with every question.",
         "README.md (Learn)")
m = MCP()

d.section("certified corrections: the certification and description writes")
code, terms = nidra("GET", "/api/terms", params={"limit": 50})
track = next(t for t in terms["terms"] if t["name"] == "Track")
code, body = nidra("PATCH", f"/api/terms/{track['id']}", json={"description_certified": True})
d.log(f"PATCH /api/terms/{{Track}} description_certified=true -> {code} {str(body)[:120]}")
d.check(code == 200, "a certified correction can be written")
if code != 200:
    d.note("same failure as demo 09: dal/owners.py _stored_owner opens Postgres, so every term and SQL-attribute "
           "edit answers 500 on Nidra")
_, after = nidra("GET", f"/api/terms/{track['id']}")
after = after.get("data", after)
if after.get("description_certified"):
    nidra("PATCH", f"/api/terms/{track['id']}", json={"description_certified": False})

d.section("usage: what the catalog records about queries")
rows = sparql(m, """SELECT ?sql ?count ?ts WHERE { GRAPH ?g { ?q a <urn:ao:sql_query> ; <urn:ao:sql_query#sql_full_query> ?sql .
  OPTIONAL { ?q <urn:ao:sql_query#total_counter> ?count } OPTIONAL { ?q <urn:ao:sql_query#last_query_timestamp> ?ts } } }""")
for r in rows:
    d.log(f"- count={term(r.get('count'))} last={term(r.get('ts'))} {str(term(r['sql']))[:90]}")
d.log(f"{len(rows)} SQL statements in the catalog (the fixture's query history)")
d.log("Usage enters the catalog only at ingest, from the source's own query log (catalog/write.py -> sql_parse.populate_queries); "
      "the Chinook ingest in demo 13 logged 'Starting to parse 0 queries' (SQLite keeps no query log).")
src = open(os.path.join(FORK, "auto_ontology/retrieval/text_to_sql/agents/sql_from_semantic.py")).read()
writers = subprocess.run(["grep", "-rn", "similar_questions", os.path.join(FORK, "auto_ontology")], capture_output=True, text=True).stdout
assigned = [ln for ln in writers.splitlines() if "/tests/" not in ln and ("similar_questions\"] =" in ln or "similar_questions'] =" in ln)]
d.log(f"chat answers are not folded back: sql_from_semantic.py reads path_state['similar_questions'] "
      f"(\"Using N similar questions from conversations\"), and no code assigns it ({len(assigned)} writers found); "
      "the slot is always empty, upstream too.")
d.check(False, "an answered question improves the next one")
d.finish("not yet", "certification writes fail on Nidra (500); usage is only the source's query log at ingest; "
         "nothing folds answers back, upstream included")
