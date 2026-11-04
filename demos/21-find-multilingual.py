"""Beyond: find, across languages, over the catalog's own RDF."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import GRAPH, MCP, Demo, nidra, sparql, term  # noqa: E402

d = Demo("21-find-multilingual", "find a column by meaning, in Spanish and Japanese",
         "gardend's find searches every entity of the graph by meaning and words in any language "
         "(multilingual-e5-small) and returns the named graph and how to read the hit. Upstream's search_terms "
         "matches term names as substrings (demo 09).")
m = MCP()
cases = [
    ("es", "duración de la canción en milisegundos", "Milliseconds", "Track"),
    ("ja", "曲の長さ（ミリ秒）", "Milliseconds", "Track"),
    ("es", "tarifa de alquiler de la película", "rental_rate", "film"),
    ("ja", "映画のレンタル料金", "rental_rate", "film"),
    ("es", "¿quién atendió al cliente? el empleado de soporte", "SupportRepId", "Customer"),
]
for lang, q, want_col, want_table in cases:
    d.section(f"[{lang}] {q}")
    ok, v = m.call("find", {"graphId": GRAPH, "query": q, "k": 5})
    hits = v.get("hits") or []
    for h in hits:
        d.log(f"  {h['score']:.2f} {h['type']:16s} {h['label']:22s} {h['says'][:70]}")
    top = hits[0] if hits else {}
    tbl = None
    if top:
        r = sparql(m, f"SELECT ?t WHERE {{ GRAPH ?g {{ <{top['iri']}> <urn:ao:catalog_column#ref-table_id> ?x . ?x <urn:ao:catalog_table#name> ?t }} }}")
        tbl = term(r[0]["t"]) if r else None
    d.check(ok and top.get("label") == want_col and (tbl in (None, want_table)), f"top hit is {want_table}.{want_col} (got {tbl}.{top.get('label')})")
    if lang == "es" and want_col == "Milliseconds":
        d.log(f"  provenance: {v.get('provenance')}; next: {v.get('next', '')[:110]}…")

d.section("the same intent through the engine's glossary search")
for q in ("canción", "曲", "song"):
    code, body = nidra("GET", "/api/terms", params={"query": q})
    d.log(f"  GET /api/terms?query={q} -> {[t['name'] for t in body.get('terms', [])]}")
d.log("(search_terms is a name substring match; 'song' is Track's synonym and still finds nothing there)")
d.finish()
