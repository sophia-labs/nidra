"""Parity: the catalog (databases, schemas, tables, columns, FKs) and join paths."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, nidra  # noqa: E402

d = Demo("10-catalog", "catalog: tables, columns, FKs, join paths",
         "A semantic catalog over relational sources; describe_table is the one table view MCP exposes; "
         "the management skill names term-to-term semantic paths.",
         "README.md § Key Features; docs/mcp.md (describe_table); skills/nvidia-ontology-management § Semantic relationships")

d.section("GET /api/datasources/dbs, /api/schemas/{db}, /api/tables/{schema}")
code, dbs = nidra("GET", "/api/datasources/dbs")
total_tables = 0
by_name = {}
for db in dbs.get("data", []):
    code_s, schemas = nidra("GET", f"/api/schemas/{db['id']}")
    for sc in (schemas.get("schemas") or schemas.get("data") or []):
        sc["name"] = sc.get("schema_name") or sc.get("name")
        code_t, tables = nidra("GET", f"/api/tables/{sc['id']}")
        names = [t["name"] for t in (tables.get("data") or [])]
        total_tables += len(names)
        for t in tables.get("data") or []:
            by_name[(db["name"], t["name"])] = t
        d.log(f"- {db['name']}.{sc['name']}: {len(names)} tables: {', '.join(sorted(names))[:300]}")
d.check(total_tables >= 30, f"{total_tables} tables across both databases")

d.section("GET /api/columns/{table_id}: Chinook Track")
track = by_name.get(("chinook", "Track"))
code, cols = nidra("GET", f"/api/columns/{track['id']}")
cl = (cols.get("data") or {}).get("columns") or []
for c in cl:
    d.log(f"- {c.get('column_name')} {c.get('data_type')}")
d.check(code == 200 and len(cl) == 9, f"Track has {len(cl)} columns (Chinook's Track has 9)")

d.section("describe_table: GET /api/exploration/tables/{id}/details for Pagila film")
film = by_name.get(("pagila", "film"))
code, det = nidra("GET", f"/api/exploration/tables/{film['id']}/details")
data = det.get("data", det)
d.show(f"film details ({code}), keys", sorted(data.keys()) if isinstance(data, dict) else data, 800)
d.show("film details (abridged)", data, 3000)
d.check(code == 200, "describe_table answers for film")

d.section("foreign keys: GET /api/exploration/edges")
code, edges = nidra("GET", "/api/exploration/edges", params={"limit": 200})
el = edges.get("data") or []
fk = [e for e in el if e.get("via_foreign_key")]
for e in fk[:8]:
    for k in e.get("foreign_keys", [])[:1]:
        d.log(f"- {k.get('source_table', '?')}.{k.get('source_column')} -> {k.get('target_table', '?')}.{k.get('target_column')}")
d.check(code == 200 and len(fk) >= 20, f"{len(fk)} table-to-table edges via foreign keys")

d.section("join path between two terms: GET /api/exploration/terms/{a}/path/{b}")
code, terms = nidra("GET", "/api/terms", params={"limit": 50})
tid = {t["name"]: t["id"] for t in terms.get("terms", [])}
for a, b in (("Customer", "Rental"), ("Payment", "Rental"), ("Customer", "Film")):
    code, path = nidra("GET", f"/api/exploration/terms/{tid[a]}/path/{tid[b]}")
    hops = ((path or {}).get("data") or {}).get("hops") or []
    chain = " -> ".join([hops[0]["source"]["name"]] + [f"[{h['relationship']}] {h['target']['name']} ({h['target']['type']})" for h in hops]) if hops else "(no path)"
    d.log(f"{a} -> {b} ({code}): {len(hops)} hops: {chain}")
    if (a, b) == ("Customer", "Rental"):
        d.check(code == 200 and len(hops) > 0, "a semantic path Customer -> Rental exists (the fixture's semantic FK)")
d.log("(the fixture defines semantic FKs among Customer, Payment and Rental only, so Customer -> Film has no path)")

d.section("the catalog graph: GET /api/exploration/graph and /api/exploration/semantic-graph")
code, g = nidra("GET", "/api/exploration/graph")
gd = g.get("data", {})
d.log(f"graph: {len(gd.get('nodes', []))} nodes, {len(gd.get('edges', gd.get('links', [])))} edges ({code})")
code2, sg = nidra("GET", "/api/exploration/semantic-graph")
sgd = sg.get("data", sg)
d.log(f"semantic-graph ({code2}): keys {sorted(sgd.keys()) if isinstance(sgd, dict) else type(sgd).__name__}")
d.check(code == 200 and code2 == 200, "both graph views answer")

d.section("global search: POST /api/search/global-search")
code, gs = nidra("POST", "/api/search/global-search", json={"search_term": "rental"})
d.show(f"global-search 'rental' ({code})", gs, 1200)
d.check(code == 200, "global search answers")
if code == 500:
    d.note("POST /api/search/global-search answers 500 on Nidra: NotImplementedError: "
           "auto_ontology.dal.search.fetch_global_search has no RDF port (AO_RDF_STRICT=1)")
d.finish()
