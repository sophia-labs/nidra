"""Beyond: a correction as witnessed testimony (Emporium). The glossary says
term Track is 'A single piece of recorded music.'; Chinook's own rows say 214
of its 3,503 tracks are MPEG-4 video files. The demo records what the
glossary says (shared commons) and a named witness's correction that
supersedes it (the witness's membrane), then reads the heads: storage keeps
both and picks no winner. LABELLED WRITE: two sophia-memory-core records in
graph ao-catalog's memory projection (resubmitting converges, nothing doubles)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import GRAPH, MCP, Demo, chinook, nidra, short  # noqa: E402

WITNESS = "agent-6e6964726164656d"   # hex of 'nidradem': the nidra-demos witness (Claude, run on Vera's behalf)
WITNESS_NAME = "nidra-demos (Claude Fable, recording for Vera)"
VALID_FROM = 1793750000000            # fixed, so a re-run resubmits byte-identical records

d = Demo("22-emporium-correction", "a correction as witnessed testimony, and its two heads",
         "Emporium records change as lineage and testimony as attributed: a correction supersedes, never overwrites; "
         "each witness keeps its own head; storage never picks a winner (plans/2026-11-02-emporium-log/ARCHITECTURE.md §2-3).")
m = MCP()

d.section("what the glossary says now (the engine's API)")
code, terms = nidra("GET", "/api/terms", params={"limit": 50})
track = next(t for t in terms["terms"] if t["name"] == "Track")
term_iri = f"urn:ao:term/id={track['id']}"
d.log(f"{term_iri}: {track['description']!r} (description_certified={track['description_certified']})")

d.section("what the data says (Chinook, directly)")
media = chinook("SELECT m.Name, count(*) FROM Track t JOIN MediaType m ON m.MediaTypeId = t.MediaTypeId GROUP BY 1 ORDER BY 2 DESC")
total = sum(n for _, n in media)
video = sum(n for name, n in media if "video" in name.lower())
for name, n in media:
    d.log(f"  {name}: {n}")
examples = chinook("SELECT t.Name, g.Name FROM Track t JOIN MediaType m USING (MediaTypeId) JOIN Genre g USING (GenreId) WHERE m.Name LIKE '%video%' LIMIT 3")
d.log(f"  e.g. {examples}")
d.check(video > 0, f"{video} of {total} Track rows are video, so 'a single piece of recorded music' is wrong")

d.section("the vocabulary (emporium_vocab sophia-memory-core, MemoryRecord)")
ok, voc = m.call("emporium_vocab", {"graphId": GRAPH, "name": "sophia-memory-core"})
mr = next(c for c in voc.get("classCatalog", []) if c["name"] == "MemoryRecord")
d.log("MemoryRecord fields used here: content, kind, scope, contentOrientation, visibility, status, sourceRefs, "
      "isCurrent+validFrom (a head needs both, invariant I7), supersedes, tags; observedBy comes from the observer")
d.check(ok and any(f["key"] == "supersedes" for f in mr["fields"]), "MemoryRecord carries supersedes / lineage / observedBy")

base = {"scope": "graph", "kind": "ClaimMemory", "contentOrientation": "knowledge", "visibility": "shared",
        "status": "active", "isCurrent": True, "validFrom": VALID_FROM}
said = dict(base, content=f"The Auto Ontology glossary describes term Track ({term_iri}) as: \"{track['description']}\"",
            tags=["nidra-demo-22", "glossary-description", "term:Track"],
            sourceRefs=[{"sourceKind": "ArtifactReference", "externalUri": term_iri,
                         "sourceLabel": f"Nidra GET /api/terms/{track['id']} (ao-catalog)"}])

d.section("write 1: the glossary's statement, published to the shared commons")
ok, w1 = m.call("emporium_write", {"graph_id": GRAPH, "vocab": "sophia-memory-core", "records": [said], "publish": True})
d.show("emporium_write (publish)", w1, 1500)
d.check(ok and w1.get("ok"), f"recorded: {w1.get('results', [{}])[0].get('outcome')}")
said_iri = w1["results"][0]["subject"]

corrected = (f"A purchasable media item: an audio track or a video. {video} of Chinook's {total} Track rows are "
             f"'Protected MPEG-4 video file' (TV episodes such as {examples[0][0]!r}).")
fix = dict(base, content=f"Correction to the glossary description of term Track ({term_iri}): it is wrong to say "
                         f"\"{track['description']}\". Right: \"{corrected}\" Witness: {WITNESS_NAME}.",
           tags=["nidra-demo-22", "glossary-correction", "term:Track", "witness:nidra-demos"],
           supersedes=said_iri,
           sourceRefs=[{"sourceKind": "ArtifactReference", "externalUri": term_iri, "sourceLabel": "the term corrected"},
                       {"sourceKind": "ToolResult", "sourceLabel": "SQLite nidra/data/chinook.sqlite: SELECT m.Name, count(*) FROM "
                        "Track t JOIN MediaType m ON m.MediaTypeId = t.MediaTypeId GROUP BY 1 -> " + short(media, 300)}])

d.section(f"write 2: the correction, in witness {WITNESS}'s own membrane, superseding write 1")
ok, w2 = m.call("emporium_write", {"graph_id": GRAPH, "vocab": "sophia-memory-core", "records": [fix], "observer": WITNESS})
d.show("emporium_write (observer)", w2, 1500)
d.check(ok and w2.get("ok"), f"recorded: {w2.get('results', [{}])[0].get('outcome')}")
fix_iri = w2["results"][0]["subject"]

d.section("the heads")
ok, hc = m.call("emporium_heads", {"graph_id": GRAPH})
lc = [x for x in hc.get("lineages", []) if said_iri in x["heads"] or x["lineage"] == said_iri]
d.show("emporium_heads (commons)", lc, 1200)
ok, hw = m.call("emporium_heads", {"graph_id": GRAPH, "observer": WITNESS})
lw = [x for x in hw.get("lineages", []) if fix_iri in x["heads"]]
d.show(f"emporium_heads (observer {WITNESS})", lw, 1200)
same_lineage = bool(lc and lw and lc[0]["lineage"] == lw[0]["lineage"])
d.check(same_lineage, "one lineage: the commons' head is the glossary's statement, the witness's head is the correction")
ok, q = m.call("emporium_query", {"graph_id": GRAPH, "vocab": "sophia-memory-core", "class": "MemoryRecord",
                                   "perspectives": "all", "criteria": {"tag": "nidra-demo-22"}})
objs = q.get("objects", [])
for o in objs:
    pr = o.get("predicates", {})
    content = next(iter([v for k, v in pr.items() if k.endswith("#content")] or [[""]]))
    d.log(f"- head {o['subject'][-24:]} witnessGraph={o.get('witnessGraph', '')[-40:]} contested={o.get('contested')} "
          f"siblingHeads={[s[-12:] for s in o.get('siblingHeads') or []]}")
    d.log(f"    {str(content)[:170]}")
two = len(objs) == 2 and any(o.get("contested") for o in objs)
d.check(two, "merged across perspectives there are two current heads, flagged contested; nothing was overwritten")

ok, desc = m.call("emporium_describe", {"graphId": GRAPH, "iri": fix_iri})
d.show("emporium_describe (the correction): testimony", {k: desc.get(k) for k in ("class", "title", "testimony", "stance")}, 1500)
from demo import sparql, term  # noqa: E402
att = sparql(m, f"SELECT ?g ?by ?sup ?lin WHERE {{ GRAPH ?g {{ <{fix_iri}> <http://mnemosyne.dev/memory#observedBy> ?by ; "
                f"<http://mnemosyne.dev/memory#supersedes> ?sup ; <http://mnemosyne.dev/memory#lineage> ?lin }} }}")
for r in att:
    d.log(f"attribution (SPARQL on the record): observedBy {term(r['by'])}, supersedes …{term(r['sup'])[-16:]}, "
          f"lineage …{term(r['lin'])[-16:]}, in named graph {term(r['g'])}")
d.check(bool(att) and WITNESS in str(att[0]["by"]), f"the correction is attributed to witness {WITNESS}")
if not desc.get("testimony"):
    d.log("(emporium_describe fills 'testimony' for workflow-run items; for a memory record the witness is mem:observedBy, read above)")
ok, viol = m.call("emporium_violations", {"graph_id": GRAPH, "limit": 5})
d.log(f"emporium_violations: {len(viol.get('violations', []))} findings in the ledger")

d.section("and the engine?")
code, now = nidra("GET", f"/api/terms/{track['id']}")
now = now.get("data", now)
d.log(f"GET /api/terms/{{Track}} still says {now.get('description')!r}")
d.note("the correction lives beside the catalog as testimony; the text-to-SQL agent does not read Emporium heads, so "
       "Nidra's answers still use the glossary's description. Wiring heads into retrieval is not done.")
d.log(f"Witness {WITNESS} = {WITNESS_NAME}; its records live in "
      f"urn:mnemosyne:local:graph:{GRAPH}:projection:memory:agent:{WITNESS}.")
d.finish()
