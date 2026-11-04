"""Beyond: the catalog as RDF, queried with SPARQL through gardend's MCP."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import MCP, Demo, nidra, sparql, term  # noqa: E402

d = Demo("20-sparql-catalog", "the catalog as RDF: SPARQL over terms, columns, tables",
         "Nidra's catalog is one RDF graph (a W3C Direct Mapping of upstream's schema, auto_ontology/rdf/README.md), so an "
         "agent can ask it anything SPARQL can say, through MCP. Upstream's MCP exposes nine fixed tools and deliberately "
         "no catalog browser (docs/mcp.md).")
m = MCP()
P = "PREFIX t: <urn:ao:term#> PREFIX ca: <urn:ao:column_attribute#> PREFIX cat: <urn:ao:column_attribute__term#> " \
    "PREFIX cha: <urn:ao:column__has_attribute#> PREFIX col: <urn:ao:catalog_column#> PREFIX tb: <urn:ao:catalog_table#> " \
    "PREFIX sc: <urn:ao:catalog_schema#> PREFIX db: <urn:ao:catalog_database#> "

d.section("1. every glossary term, its column attributes, the physical column, table and database (one query)")
q1 = P + """SELECT ?term ?attr ?column ?table ?schema ?database WHERE { GRAPH ?g {
  ?tl cat:ref-term_id ?t ; cat:ref-attribute_id ?a .
  ?t t:name ?term . ?a ca:name ?attr .
  ?hl cha:ref-attribute_id ?a ; cha:ref-column_id ?c .
  ?c col:name ?column ; col:ref-table_id ?tbl .
  ?tbl tb:name ?table ; tb:ref-schema_id ?s .
  ?s sc:name ?schema ; sc:ref-database_id ?d . ?d db:name ?database .
} } ORDER BY ?term ?attr"""
d.log(q1)
rows = sparql(m, q1)
for r in rows:
    d.log(f"  {term(r['term']):9s} {term(r['attr']):16s} -> {term(r['database'])}.{term(r['schema'])}.{term(r['table'])}.{term(r['column'])}")
d.check(len(rows) == 8, f"{len(rows)} term -> column paths (the glossary has 8 column attributes)")
code, api = nidra("GET", "/api/terms", params={"limit": 50})
api_total = sum(c["count"] for c in api.get("column_attribute_counts", []))
d.check(api_total == len(rows), f"the engine's own API agrees: {api_total} column attributes")

d.section("2. where the semantic layer is thin: tables no term represents, per database, with column counts")
q2 = P + """SELECT ?database (COUNT(?tbl) AS ?tables) (SUM(?noterm) AS ?without_term) WHERE { GRAPH ?g {
  ?tbl a <urn:ao:catalog_table> ; tb:ref-schema_id ?s . ?s sc:ref-database_id ?d . ?d db:name ?database .
  BIND(IF(EXISTS { ?x <urn:ao:table__term#ref-table_id> ?tbl }, 0, 1) AS ?noterm)
} } GROUP BY ?database ORDER BY ?database"""
rows2 = sparql(m, q2)
for r in rows2:
    d.log(f"  {term(r['database'])}: {term(r['tables'])} tables, {term(r['without_term'])} with no term")
d.check(len(rows2) == 2 and all(int(term(r["without_term"])) > 0 for r in rows2), "coverage of the semantic layer per database, in one query")

d.section("3. a foreign-key walk: what does a Pagila rental reach in two hops?")
q3 = P + """SELECT DISTINCT ?hop1 ?hop2 WHERE { GRAPH ?g {
  ?t0 tb:name "rental" . ?c0 col:ref-table_id ?t0 .
  ?f1 <urn:ao:column__foreign_key#ref-source_column_id> ?c0 ; <urn:ao:column__foreign_key#ref-target_column_id> ?c1 .
  ?c1 col:ref-table_id ?t1 . ?t1 tb:name ?hop1 .
  OPTIONAL { ?c1b col:ref-table_id ?t1 . ?f2 <urn:ao:column__foreign_key#ref-source_column_id> ?c1b ;
             <urn:ao:column__foreign_key#ref-target_column_id> ?c2 . ?c2 col:ref-table_id ?t2 . ?t2 tb:name ?hop2 . FILTER(?t2 != ?t1) }
} } ORDER BY ?hop1 ?hop2"""
rows3 = sparql(m, q3)
for r in rows3:
    d.log(f"  rental -> {term(r['hop1'])} -> {term(r.get('hop2')) or '·'}")
d.check(any(term(r["hop1"]) == "customer" for r in rows3), "rental reaches customer (and beyond) by foreign keys")

d.section("4. the same graph holds the vector index: which catalog rows are embedded, per collection")
q4 = "SELECT ?collection (COUNT(?s) AS ?n) WHERE { GRAPH ?g { ?s <urn:ao:vdb#collection> ?collection } } GROUP BY ?collection ORDER BY ?collection"
for r in sparql(m, q4):
    d.log(f"  {term(r['collection'])}: {term(r['n'])}")
d.log("Patterns are wrapped in GRAPH ?g { } because gardend's store is named-graph partitioned (an unwrapped pattern matches nothing).")
d.finish()
