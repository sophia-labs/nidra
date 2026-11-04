"""Inventory of everything the demos wrote, measured against demo 00's restore
point and N-Quads snapshot, with the exact steps to remove it. Read-only."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import GRAPH, MCP, Demo, sparql, term  # noqa: E402

d = Demo("29-undo-check", "what the demos wrote, and how to undo it",
         "Vera can undo the demos: the restore point from demo 00 plus the catalog snapshot.")
m = MCP()
state = os.path.join(os.path.dirname(__file__), "state")
latest = open(os.path.join(state, "LATEST")).read().split("\t") if os.path.exists(os.path.join(state, "LATEST")) else []
rp0, label0, snap = (latest + ["", "", ""])[:3]
snap = snap.strip()
d.log(f"demo 00 restore point: {rp0} ({label0}); snapshot: {snap}")

d.section("documents and wires (the restore point's layer)")
ok, diff = m.call("diff_restore_points", {"graphId": GRAPH, "restorePointId": rp0})
d.show("diff_restore_points demo-00 -> current", diff.get("aggregate"), 600)
ok, docs = m.call("list_documents", {"graphId": GRAPH})
mine = [x for x in docs.get("documents", []) if x["title"].startswith(("nidra demo", "Answer trace"))]
for x in mine:
    ok, w = m.call("get_wires", {"graphId": GRAPH, "documentId": x["documentId"], "direction": "both"})
    d.log(f"- {x['documentId']}  {x['title']!r}  wires: {[(y.get('predicate'), y.get('wireId') or y.get('id')) for y in w.get('wires') or []]}")
others = [x for x in docs.get("documents", []) if x not in mine]
d.check(not others, f"no documents other than the demos' ({len(mine)} demo documents, {len(others)} others)")
customer_term = term(sparql(m, 'SELECT ?s WHERE { GRAPH ?g { ?s a <urn:ao:term> ; <urn:ao:term#name> "Customer" } }')[0]["s"])
ok, w = m.call("get_wires", {"graphId": GRAPH, "iri": customer_term, "direction": "outgoing"})
d.log(f"- wire from term Customer: {[(y.get('predicate'), y.get('wireId') or y.get('id')) for y in w.get('wires') or []]} (drawn on the demo 24 board)")

d.section("restore points the demos made")
ok, rps = m.call("list_restore_points", {"graphId": GRAPH, "limit": 50})
for r in rps.get("restorePoints", []):
    d.log(f"- {r['restorePointId']}  {r.get('label')}  {r.get('timestamp')}")

d.section("memory and Emporium records (projection graphs, RDF)")
rows = sparql(m, """SELECT ?g ?s ?tag WHERE { GRAPH ?g { ?s <http://mnemosyne.dev/memory#tag> ?tag .
  FILTER(STRSTARTS(STR(?tag), "nidra-demo")) } } ORDER BY ?tag""")
for r in rows:
    d.log(f"- {term(r['tag'])}  {term(r['s'])}  in {term(r['g'])}")

d.section("the catalog's named graph against demo 00's snapshot")
user_rdf = f"<urn:mnemosyne:local:graph:{GRAPH}:user:rdf>"
before = set(ln for ln in open(snap, encoding="utf-8").read().splitlines() if ln.strip() and ln.endswith(user_rdf + " .")) if snap and os.path.exists(snap) else set()
ok, dump = m.call("rdf_dump", {"graphId": GRAPH, "format": "nquads"})
now = set(ln for ln in (dump.get("data") or "").splitlines() if ln.strip() and ln.endswith(user_rdf + " ."))
added, removed = now - before, before - now
d.log(f"user:rdf quads: snapshot {len(before)}, now {len(now)}; added {len(added)}, removed {len(removed)}")
for ln in sorted(added)[:10]:
    d.log("  + " + ln[:200])
for ln in sorted(removed)[:10]:
    d.log("  - " + ln[:200])
d.check(bool(before), "the snapshot is there to compare against")

d.section("other graphs the demos created on the scratch gardend")
ok, gl = m.call("list_graphs", {})
for g in gl.get("graphs", []):
    if g["graph_id"].startswith("ao-demo"):
        d.log(f"- {g['graph_id']}  {g.get('title')}")

d.section("to undo (nothing here is run by the demos)")
d.log("1. documents: delete_document {graphId: ao-catalog, documentId: <each id above>} (their wires and the comment go with them)")
d.log("2. memory / Emporium records: emporium_retract {graph_id: ao-catalog, subject: <each subject above>, rationale: 'demo cleanup'}")
d.log("   (a retraction is an event; the records stay in the log, hidden from heads and recall)")
d.log("3. catalog triples, if a later run changed them: clear named graph user:rdf and rdf_load the snapshot (README)")
d.log("4. scratch graphs: manage_graph {graphId: <id>, action: delete}")
d.log("restore_to_restore_point to demo 00's point is NOT enough: in demo 23 a restore left a later document and later RDF in place.")
d.finish()
