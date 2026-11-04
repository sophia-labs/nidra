"""Beyond: a restore point before a recompile, and the diff after. Also: what a
restore point does and does not hold."""
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import GRAPH, MCP, Demo, nidra  # noqa: E402

d = Demo("23-restore-point-diff", "restore point, recompile, diff",
         "gardend captures time-travel restore points of a graph and diffs any two, so a recompile can be inspected "
         "and undone. Upstream has no snapshot of its semantic layer (the management skill's publication gate asks "
         "for a scoped backup by hand).")
m = MCP()
USER_RDF = f"urn:mnemosyne:local:graph:{GRAPH}:user:rdf"


def fingerprint():
    ok, dump = m.call("rdf_dump", {"graphId": GRAPH, "format": "nquads"})
    lines = sorted(ln for ln in (dump.get("data") or "").splitlines() if ln.strip() and USER_RDF in ln)
    return len(lines), hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16], set(lines)


d.section("before: restore point 1 and a fingerprint of the catalog's named graph")
ok, rp1 = m.call("create_restore_point", {"graphId": GRAPH, "label": "nidra-demo-23 before recompile"})
d.show("create_restore_point", rp1, 600)
n1, h1, s1 = fingerprint()
d.log(f"catalog ({USER_RDF}): {n1} quads, sha256 {h1}")

d.section("the recompile: POST /api/semantic-compilation/trigger, then wait 30 s")
since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
code, trig = nidra("POST", "/api/semantic-compilation/trigger")
d.log(f"trigger -> {code} {trig}")
time.sleep(30)
logs = subprocess.run(["docker", "logs", "--since", since, "nidra-ingestion"], capture_output=True, text=True, timeout=30)
sem = [ln for ln in (logs.stdout + logs.stderr).splitlines() if "semantic:" in ln]
for ln in sem[-4:]:
    d.log("  " + ln[:200])

d.section("after: restore point 2, the restore-point diff, and the catalog fingerprint")
ok, rp2 = m.call("create_restore_point", {"graphId": GRAPH, "label": "nidra-demo-23 after recompile"})
ok, diff = m.call("diff_restore_points", {"graphId": GRAPH, "restorePointId": rp1["restorePointId"], "against": rp2["restorePointId"]})
d.show("diff_restore_points rp1 -> rp2", diff, 2000)
d.check(ok, "the two restore points diff")
n2, h2, s2 = fingerprint()
d.log(f"catalog after: {n2} quads, sha256 {h2}; added {len(s2 - s1)}, removed {len(s1 - s2)}")
for ln in sorted(s2 - s1)[:5]:
    d.log("  + " + ln[:180])
for ln in sorted(s1 - s2)[:5]:
    d.log("  - " + ln[:180])
compiled = any("skipping run" not in ln and "compil" in ln for ln in sem) and h1 != h2
if not compiled:
    d.note("the recompile was a no-op: compilation is switched off on the live stack (demo 11), so the catalog did not "
           "change and there is nothing for a diff to show")

d.section("what a restore point holds")
ok, man = m.call("get_restore_point", {"graphId": GRAPH, "restorePointId": rp2["restorePointId"]})
d.show("get_restore_point (manifest)", {k: man.get(k) for k in ("workspace", "documents", "metadata")}, 1200)
d.note("a restore point bundles the workspace (folders, wires) and a snapshot of every document; the catalog is RDF "
       "loaded straight into the store (/api/rdf/load, /api/sparql/update), outside any document, so neither the "
       "restore point nor diff_restore_points sees it. The fingerprint above is the diff that sees it.")

d.section("does restore_to_restore_point undo? (on a throwaway graph made for this, never on ao-catalog)")
import datetime as dt  # noqa: E402

scratch = f"ao-demo-rp-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
m.call("create_graph", {"graphId": scratch, "title": "nidra demo 23: restore probe (scratch)"})
ok, rpa = m.call("create_restore_point", {"graphId": scratch, "label": "empty"})
m.call("rdf_load", {"graphId": scratch, "format": "ntriples", "data": "<urn:demo23:x> <urn:demo23:p> \"loaded after the restore point\" .\n"})
ok, doc = m.call("create_document", {"graphId": scratch, "title": "made after the restore point"})
m.call("write_document", {"graphId": scratch, "documentId": doc["documentId"], "content": "# After\n\nwritten after the restore point", "format": "markdown"})
ok, op = m.call("restore_to_restore_point", {"graphId": scratch, "restorePointId": rpa["restorePointId"]})
st = {}
for _ in range(30):
    time.sleep(1)
    ok, st = m.call("get_restore_operation", {"graphId": scratch, "operationId": op.get("operationId")})
    if st.get("state") in ("succeeded", "failed"):
        break
ok, q = m.call("sparql_query", {"graphId": scratch, "query": "SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { <urn:demo23:x> ?p ?o } }"})
raw_left = "\"0\"" not in str(q.get("rows"))
ok, docs = m.call("list_documents", {"graphId": scratch})
doc_left = any(x["documentId"] == doc["documentId"] for x in docs.get("documents", []))
d.log(f"restore {st.get('state')} ({st.get('message')}); raw triple still there: {raw_left}; later document still listed: {doc_left}")
d.check(not raw_left, "restore removes RDF loaded after the restore point")
d.check(not doc_left, "restore removes a document created after the restore point")
d.note(f"on {scratch}: after restore_to_restore_point to a point taken on the empty graph, the RDF loaded later "
       f"{'is still there' if raw_left else 'is gone'} and the document created later {'is still listed' if doc_left else 'is gone'}. "
       "So the restore point from demo 00 is not, by itself, an undo of what the demos add: demo 29 lists the "
       "documents, wires and records they wrote, and the catalog's own undo is demo 00's N-Quads snapshot.")
d.check(compiled, "a recompile ran and its diff is visible in the restore-point diff")
d.finish("partial", "restore points are captured and diffed; they do not cover the RDF catalog, a restore left both "
         "later RDF and a later document in place, and the recompile is switched off on the live stack")
