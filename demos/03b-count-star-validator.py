"""Why a plain count can fail (demo 03): the SQL validator refuses SQL that
names no column, such as SELECT COUNT(*) FROM "Track". Deterministic: no model
call, upstream's own validation function on Nidra's catalog."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo  # noqa: E402

import auto_ontology.dal  # noqa: E402,F401  (installs the RDF read ports)
from auto_ontology.dal.datasources import fetch_all_schema_ids  # noqa: E402
from auto_ontology.retrieval.data_access.graph_schemas import get_schemas_by_ids  # noqa: E402
from auto_ontology.retrieval.text_to_sql.agents.sql_parse_validation import SQLValidationAgent  # noqa: E402

d = Demo("03b-count-star-validator", "the validator and COUNT(*)",
         "Validate: generated SQL is checked against the catalog before it runs.",
         "README.md (Validate); auto_ontology/retrieval/text_to_sql/agents/sql_parse_validation.py")
schemas = get_schemas_by_ids(fetch_all_schema_ids())
probe = {}
for sql in ('SELECT COUNT(*) AS track_count FROM "Track"', 'SELECT COUNT(*) AS track_count FROM Track',
            'SELECT COUNT("TrackId") AS track_count FROM "Track"', 'SELECT COUNT(*) FROM "Track" WHERE "Milliseconds" > 300000',
            "SELECT COUNT(*) FROM film", "SELECT COUNT(film_id) FROM film", "SELECT * FROM film_category"):
    r = SQLValidationAgent._sql_parse_validation(schemas, sql, ["sqlite", "postgres"])
    probe[sql] = r.get("error") or f"ok, tables {r.get('used_tables')}"
    d.log(f"  {sql:58s} -> {probe[sql]}")
star = [k for k in probe if "COUNT(*)" in k and "WHERE" not in k]
rejected = all("known to the catalog" in probe[k] for k in star)
d.check(not rejected, "a count over a whole known table passes validation")
if rejected:
    d.note("SQL that references no column (COUNT(*) over a table, with no WHERE) is refused as 'SQL doesn\'t reference any "
           "table known to the catalog': upstream's auto_ontology/catalog/sql_parse.py parse_query_single returns None "
           "when parse_query_slim finds no column, and the validator reads None as 'no known table'. The engine's counting "
           "prompt asks for COUNT(pk), so most runs pass; when the model writes COUNT(*) it is refused, rewrites COUNT(*) "
           "again, and after the reconstruction budget answers 'SQL can't be constructed' (demo 03, 223 s). "
           "The function is upstream's, unchanged in the fork; reproduced on Nidra, not run against upstream's Postgres backend.")
d.finish()
