"""Prelude: a restore point and a full N-Quads snapshot of graph ao-catalog,
taken before any demo writes. The restore point covers documents, folders and
wires (what the beyond demos add); the snapshot covers the RDF catalog itself,
which a restore point does not hold (see demo 23)."""
import datetime as dt
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import GRAPH, MCP, Demo, sparql, term  # noqa: E402

d = Demo("00-restore-point", "restore point and catalog snapshot before the demos",
         "Vera can undo everything the demos write.")
m = MCP()
stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
label = os.environ.get("DEMOS_RESTORE_LABEL") or f"nidra-demos-before-{stamp}"

d.section("restore point (documents, folders, wires)")
ok, rp = m.call("create_restore_point", {"graphId": GRAPH, "label": label})
d.show("create_restore_point", rp)
d.check(ok and bool(rp.get("restorePointId")), f"restore point created: {rp.get('restorePointId')} label {label}")

d.section("N-Quads snapshot of every named graph (the catalog lives here)")
ok, dump = m.call("rdf_dump", {"graphId": GRAPH, "format": "nquads"})
data = dump.get("data", "") if ok else ""
state = os.path.join(os.path.dirname(__file__), "state")
os.makedirs(state, exist_ok=True)
path = os.path.join(state, f"{GRAPH}-{label}.nq")
with open(path, "w", encoding="utf-8") as fh:
    fh.write(data)
lines = [ln for ln in data.splitlines() if ln.strip()]
d.log(f"wrote {len(lines)} quads to {path} (sha256 {hashlib.sha256(data.encode()).hexdigest()[:16]}…)")
d.check(ok and len(lines) > 1000, f"snapshot holds {len(lines)} quads")

d.section("baseline counts")
rows = sparql(m, "SELECT ?g (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } } GROUP BY ?g ORDER BY ?g")
for r in rows:
    d.log(f"  {r['g']}: {term(r['n'])}")
with open(os.path.join(state, "LATEST"), "w") as fh:
    fh.write(f"{rp.get('restorePointId')}\t{label}\t{path}\n")
d.log("")
d.log("To undo the documents, wires and comments the demos add:")
d.log(f"  restore_to_restore_point {{graphId: {GRAPH}, restorePointId: {rp.get('restorePointId')}, dryRun: true}} first, then without dryRun")
d.log("To undo catalog (RDF) changes: clear the graph's user:rdf named graph and rdf_load the snapshot above (see README).")
d.finish()
