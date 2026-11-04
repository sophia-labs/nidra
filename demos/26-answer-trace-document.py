"""Beyond: one question's answer trace kept as a document in the graph (steps,
SQL, rows), wired to the column it used, and found again by search.
LABELLED WRITE: one document 'Answer trace: …' in ao-catalog and one wire."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from answers import get_answer  # noqa: E402
from demo import GRAPH, MCP, Demo, sparql, term  # noqa: E402

d = Demo("26-answer-trace-document", "an answer trace as a document, found by search",
         "Every answer can be kept where the catalog lives, as a document with its provenance, wired to the columns "
         "it read, and found later by title, by content, or from the column. Upstream streams the trace to the "
         "caller and keeps conversations in the frontend's Postgres.")
m = MCP()
a = get_answer()
d.log(f"answer source: {a.get('source')}")
title = f"Answer trace: {a['question']}"
steps = [s["node"] for s in a.get("steps", []) if s.get("node")]
body = "\n".join([
    f"# {title}", "",
    f"Engine: {a.get('engine')} (Nidra), answer {a.get('source')}.", "",
    "## Steps", "", *[f"{i + 1}. {n}" for i, n in enumerate(steps)], "",
    "## SQL", "", "```sql", a["final_sql"], "```", "",
    "## Rows", "", "```json", json.dumps(a["rows"], indent=1), "```", "",
    "## Answer", "", a.get("answer_text") or "", "",
])


def find_doc(t):
    ok, v = m.call("search_documents", {"graphId": GRAPH, "query": t, "mode": "exact"})
    for x in v.get("results") or v.get("documents") or []:
        if x.get("title") == t:
            return x.get("documentId") or x.get("id")
    return None


d.section("create_document + write_document")
doc = find_doc(title)
if not doc:
    ok, c = m.call("create_document", {"graphId": GRAPH, "title": title})
    doc = c["documentId"]
ok, w = m.call("write_document", {"graphId": GRAPH, "documentId": doc, "content": body, "format": "markdown", "title": title})
d.log(f"document {doc}: revision {w.get('revision')}, {len(w.get('blockIds') or [])} blocks")
d.check(ok, "trace document written")

millis = term(sparql(m, 'SELECT ?s WHERE { GRAPH ?g { ?s <urn:ao:catalog_column#name> "Milliseconds" } }')[0]["s"])
ok, wires = m.call("get_wires", {"graphId": GRAPH, "documentId": doc, "direction": "outgoing"})
if not any(x.get("targetIri") == millis for x in wires.get("wires") or []):
    ok, cw = m.call("create_wires", {"graphId": GRAPH, "sourceDocumentId": doc, "targetIri": millis, "predicate": "read-column"})
    d.check(ok, "wire trace --read-column--> Track.Milliseconds")

d.section("found again")
ok, s1 = m.call("search_documents", {"graphId": GRAPH, "query": "Answer trace"})
titles = [x.get("title") for x in s1.get("results") or s1.get("documents") or []]
d.log(f"search_documents 'Answer trace': {titles}")
d.check(title in titles, "search_documents finds it by title")
ok, s2 = m.call("search_blocks", {"graphId": GRAPH, "query": "Milliseconds > 300000", "mode": "lexical"})
blocks = s2.get("blocks") or s2.get("results") or []
d.log(f"search_blocks 'Milliseconds > 300000': {[(b.get('documentId'), str(b.get('text') or b.get('snippet'))[:60]) for b in blocks][:4]}")
d.check(any(b.get("documentId") == doc for b in blocks), "search_blocks finds the SQL inside it")
ok, bl = m.call("get_backlinks", {"graphId": GRAPH, "iri": millis})
d.log(f"get_backlinks of the column: {[(x.get('kind'), x.get('sourceTitle'), x.get('predicate')) for x in bl.get('backlinks') or []]}")
d.check(any(x.get("sourceDocumentId") == doc for x in bl.get("backlinks") or []), "from the column, the trace is one backlink away")
d.log(f"made by this demo: document {doc} (see demo 29 to remove it)")
d.finish()
