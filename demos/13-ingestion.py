"""Parity: ingestion of a source (the ingestion service's POST /ingest)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, MCP, sparql, term  # noqa: E402
from ingest import trigger  # noqa: E402

d = Demo("13-ingestion", "ingest a source: POST /ingest on the ingestion service",
         "The ingestion service catalogues a connected source (tables, columns, keys, embeddings) and re-sweeps it on a schedule.",
         "README.md § Software Components (ingestion worker :3002); nidra/README.md (INGEST_INTERVAL_HOURS)")
m = MCP()
Q = """SELECT ?kind ?id WHERE { GRAPH ?g {
  ?db a <urn:ao:catalog_database> ; <urn:ao:catalog_database#name> "chinook" .
  ?sc <urn:ao:catalog_schema#ref-database_id> ?db .
  ?t <urn:ao:catalog_table#ref-schema_id> ?sc .
  { ?t <urn:ao:catalog_table#id> ?id . BIND("table" AS ?kind) }
  UNION { ?c <urn:ao:catalog_column#ref-table_id> ?t ; <urn:ao:catalog_column#id> ?id . BIND("column" AS ?kind) }
} }"""


def snapshot():
    rows = sparql(m, Q, maxRows=5000)
    out = {"table": set(), "column": set()}
    for r in rows:
        out[term(r["kind"])].add(term(r["id"]))
    return out


before = snapshot()
fk_q = "SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?f a <urn:ao:column__foreign_key> } }"
fk_before = int(term(sparql(m, fk_q)[0]["n"]))
d.log(f"before: chinook {len(before['table'])} tables, {len(before['column'])} columns; {fk_before} FK rows in the catalog")

d.section("docker exec nidra-ingestion: POST http://localhost:3002/ingest {\"database\": \"chinook\"}")
res = trigger("chinook")
d.log(f"reply: {res['reply']}")
d.log(f"finished after {res['seconds']}s: {res['finished']}")
for ln in res["log"][-14:]:
    d.log("  " + ln[:240])
d.check(res["reply"].startswith("202") and res["finished"] and "successfully" in res["finished"], "ingest accepted and finished successfully")

after = snapshot()
fk_after = int(term(sparql(m, fk_q)[0]["n"]))
d.log(f"after: chinook {len(after['table'])} tables, {len(after['column'])} columns; {fk_after} FK rows")
d.check(len(after["table"]) == 11 and len(after["column"]) == 64, "Chinook catalogued as 11 tables and 64 columns")
d.check(before == after, "re-ingest is a diff: every table and column keeps its id (nothing re-minted)")
d.check(fk_before == fk_after, "foreign keys unchanged")

d.section("the same for Pagila")
res = trigger("pagila", timeout=60)
d.log(f"reply: {res['reply']}; outcome: {res['finished']}")
if res["finished"] and "skipped" in res["finished"]:
    d.note("the ingestion service cannot sweep Pagila: its CONNECTION_STRINGS names Chinook only (demo 02), "
           "so POST /ingest for pagila is accepted (202) and then skipped: " + res["finished"].split(": ", 1)[-1])
d.finish(None, "Chinook ingests; Pagila has no connector in the ingestion service" if res["finished"] and "skipped" in res["finished"] else "")
