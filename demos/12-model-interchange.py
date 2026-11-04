"""Parity: model interchange: export (native YAML, Apache Ossie YAML, Ossie zip
with ontology) and re-import, with exact readback. The imports run against a
sandbox copy of the graph (duplicate_graph), so ao-catalog is only read."""
import datetime as dt
import io
import os
import subprocess
import sys
import zipfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import GRAPH, MCP, OUT, Demo, nidra, nidra_at, sparql, term  # noqa: E402

d = Demo("12-model-interchange", "model export (YAML, Ossie) and re-import",
         "POST /api/model/export writes the catalog + semantic layer as native YAML or Apache Ossie; "
         "POST /api/model/import reads either back; verify by exact re-export.",
         "skills/nvidia-ontology-management § Bulk import / export; references/publication.md")
here = os.path.dirname(__file__)


def norm(doc: dict) -> dict:
    """The semantic layer and catalog shape, without volatile stamps."""
    vol = {"created", "modified", "pulled", "last_query_timestamp", "imported_id", "created_by", "modified_by"}

    def strip(x):
        if isinstance(x, dict):
            # sql_column_is is a set of column ids; its order is not stable on the RDF store
            return {k: (sorted(v) if k == "sql_column_is" and isinstance(v, list) else strip(v))
                    for k, v in x.items() if k not in vol}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip(doc)


d.section("export from the live engine (read-only)")
code, native = nidra("POST", "/api/model/export", json={"databases": [], "format": "auto_ontology"})
d.check(code == 200 and "data_layer:" in native, f"native export ({code}, {len(native)} bytes)")
with open(os.path.join(OUT, "12-model.yaml"), "w") as fh:
    fh.write(native)
code, ossie = nidra("POST", "/api/model/export", json={"databases": [], "format": "ossie"})
d.check(code == 200 and "datasets:" in ossie, f"Apache Ossie export ({code}, {len(ossie)} bytes)")
with open(os.path.join(OUT, "12-model-ossie.yaml"), "w") as fh:
    fh.write(ossie)
r_zip = nidra("POST", "/api/model/export", json={"databases": [], "format": "ossie", "include_ontology": True})
code_z = r_zip[0]
import httpx  # noqa: E402
zr = httpx.post(os.environ.get("NIDRA_URL", "http://127.0.0.1:3001") + "/api/model/export",
                json={"databases": [], "format": "ossie", "include_ontology": True},
                headers={"x-auto-ontology-user-id": "vera"}, timeout=120)
names = []
if zr.status_code == 200:
    with open(os.path.join(OUT, "12-ossie-model.zip"), "wb") as fh:
        fh.write(zr.content)
    names = zipfile.ZipFile(io.BytesIO(zr.content)).namelist()
d.log(f"Ossie zip with include_ontology ({zr.status_code}): {names}")
d.check(zr.status_code == 200 and len(names) == 3, "Ossie zip holds the semantic model, the ontology and the mapping")
nat = yaml.safe_load(native)
sl = nat.get("semantic_layer", {})
d.log(f"native: {len(nat['data_layer']['databases'])} databases, {len(sl.get('terms', []))} terms, "
      f"sql_attributes {sum(len(v) for v in (sl.get('sql_attributes') or {}).values())}, "
      f"custom_analyses {len(sl.get('custom_analyses') or [])}")
oss = yaml.safe_load(ossie)
d.log(f"ossie: {len(oss.get('datasets', []))} datasets, {len(oss.get('relationships', []) or [])} relationships, "
      f"metrics {len(oss.get('metrics', []) or [])}")

d.section("sandbox: duplicate the graph, start an engine on it")
m = MCP()
sandbox = f"ao-demo-sandbox-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
ok, dup = m.call("duplicate_graph", {"source_graph_id": GRAPH, "new_graph_id": sandbox, "new_title": "nidra demos: model interchange sandbox"})
d.show("duplicate_graph", dup, 800)
d.check(ok, f"sandbox {sandbox} created")
count = lambda g: int(term(sparql_g(g, "SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }")[0]["n"]))  # noqa: E731


def show_diff(label, before, after, limit=60):
    import difflib
    x = yaml.safe_dump(norm(before), sort_keys=True).splitlines()
    y = yaml.safe_dump(norm(after), sort_keys=True).splitlines()
    diff = list(difflib.unified_diff(x, y, "original-export", label, lineterm="", n=1))
    d.log(f"diff original-export -> {label}: {len(diff)} lines (first {limit}):")
    for ln in diff[:limit]:
        d.log("  " + ln)


def sparql_g(g, q):
    ok, v = m.call("sparql_query", {"graphId": g, "query": q})
    return v.get("rows") or []


d.log(f"quads: {GRAPH} {count(GRAPH)}, {sandbox} {count(sandbox)}")
port = "3012"
env = dict(os.environ, SIDE_ENGINE_PORT=port)
up = subprocess.run(["bash", os.path.join(here, "lib", "side-engine.sh"), "start", sandbox], env=env, capture_output=True, text=True, timeout=200)
d.log(up.stdout.strip() or up.stderr.strip()[-400:])
SB = f"http://127.0.0.1:{port}"
try:
    d.section("baseline: export from the sandbox engine itself (it has Pagila connected, so dialects are filled)")
    code, base_txt = nidra_at(SB, "POST", "/api/model/export", json={"databases": [], "format": "auto_ontology"})
    base = yaml.safe_load(base_txt)
    if norm(base) != norm(nat):
        show_diff("sandbox-baseline", nat, base, 12)
        d.log("(the live engine exports Pagila's dialect as '' because it has no Pagila connection)")
    nat = base
    native = base_txt
    d.section("re-import the native export into the sandbox (replace=false, embed=false)")
    code, imp = nidra_at(SB, "POST", "/api/model/import?replace=false&embed=false", files={"file": ("model.yaml", native, "application/x-yaml")}, timeout=300)
    d.show(f"import native ({code})", imp, 2000)
    created = ((imp or {}).get("summary") or {}).get("created") or {}
    d.check(code == 200 and created and all(v == 0 for v in created.values()), "re-importing its own export creates nothing (every row adopted)")
    code, again = nidra_at(SB, "POST", "/api/model/export", json={"databases": [], "format": "auto_ontology"})
    same = norm(yaml.safe_load(again)) == norm(nat)
    d.check(code == 200 and same, "re-export after the native import equals the original export (volatile stamps aside)")
    if not same:
        show_diff("after-native-import", nat, yaml.safe_load(again))

    d.section("import the Apache Ossie export into the sandbox (replace=false, embed=false)")
    code, imp2 = nidra_at(SB, "POST", "/api/model/import?replace=false&embed=false", files={"file": ("ossie.yaml", ossie, "application/x-yaml")}, timeout=300)
    d.show(f"import ossie ({code})", imp2, 2500)
    s2 = (imp2 or {}).get("summary") or {}
    d.check(code == 200 and s2.get("format") == "ossie", "the Ossie document is accepted as Ossie")
    code, after = nidra_at(SB, "POST", "/api/model/export", json={"databases": [], "format": "auto_ontology"})
    a = yaml.safe_load(after)
    t0 = sorted(t["name"] for t in sl.get("terms", []))
    t1 = sorted(t["name"] for t in a.get("semantic_layer", {}).get("terms", []))
    d.log(f"terms before: {t0}")
    d.log(f"terms after the Ossie import: {t1}")
    d.check(t0 == t1, "the Ossie round trip neither loses nor duplicates terms")
    ca0 = sum(len(t.get("columns_attributes") or []) for t in sl.get("terms", []))
    ca1 = sum(len(t.get("columns_attributes") or []) for t in a.get("semantic_layer", {}).get("terms", []))
    d.log(f"column attributes before {ca0}, after the Ossie import {ca1}")
    same2 = norm(a) == norm(nat)
    d.check(same2, "re-export after the Ossie import equals the original export exactly")
    if not same2:
        show_diff("after-ossie-import", nat, a)
        if ca1 == ca0 + 1:
            d.note("the Ossie round trip is lossy in one place: the SQL attribute 'customer lifetime value' (an aggregate, "
                   "SELECT customer_id, sum(amount) FROM payment GROUP BY customer_id) is exported as an Ossie field and "
                   "comes back as a new column attribute 'customer_lifetime_value' on Customer, beside the SQL attribute "
                   "it came from (upstream's convert_ossie_to_auto_ontology; replace=false adopts everything else)")
finally:
    subprocess.run(["bash", os.path.join(here, "lib", "side-engine.sh"), "stop"], env=env, capture_output=True, text=True)
d.log(f"sandbox graph {sandbox} is left on the scratch gardend (manage_graph action=delete removes it)")
d.finish()
